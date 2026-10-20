"""Command Line Interface for the Groww Review Pulse Agent.

Commands:
  pulse run       - Run end-to-end pulse generation and publication (Phase 7)
  pulse fetch     - Fetch public reviews from Play Store and App Store (Phase 1)
  pulse preprocess- Preprocess and scrub PII from reviews (Phase 2)
  pulse theme     - Discover, classify, and rank product themes (Phase 3)
  pulse publish   - Publish saved pulse to Google Docs & Gmail draft via MCP (Phase 5)
  pulse eval      - Run offline evaluation suite (Phase 6)
  pulse config    - Print resolved configuration and secrets status
"""

import contextlib
import sys
from typing import Annotated

import typer
from rich import box
from rich import print as rprint
from rich.console import Console
from rich.table import Table

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")

from pulse.compose.pipeline import run_compose_pipeline
from pulse.config import get_settings
from pulse.ingest.fetcher import AllSourcesFailedError, build_sources, fetch_all
from pulse.llm.budget import BudgetExceededError, budget_from_settings
from pulse.llm.client import get_llm_client
from pulse.models import Source
from pulse.observability.logging import configure_logging, get_logger
from pulse.preprocess.pipeline import process_and_persist_reviews
from pulse.run_store import RunStore
from pulse.theming.pipeline import run_theming_pipeline
from pulse.utils.weeks import last_completed_week, window_bounds

app = typer.Typer(
    name="pulse",
    help="Groww Weekly Review Pulse Agent — transforms public app reviews into an executive pulse.",
    no_args_is_help=True,
)
console = Console()
logger = get_logger("pulse.cli")


@app.callback()
def main(
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable verbose debug logging"),
    ] = False,
    json_logs: Annotated[
        bool,
        typer.Option("--json-logs", help="Emit logs as structured JSON"),
    ] = False,
) -> None:
    """Global CLI options."""
    level = "DEBUG" if verbose else "INFO"
    configure_logging(level=level, json_format=json_logs)


@app.command()
def config() -> None:
    """Display resolved configuration (secrets are masked)."""
    settings = get_settings()
    masked = settings.masked_dict()

    table = Table(title="Groww Review Pulse — Active Configuration")
    table.add_column("Setting", style="cyan", no_wrap=True)
    table.add_column("Resolved Value", style="magenta")

    table.add_row("LLM Provider", masked["llm_provider"])
    table.add_row("LLM API Key", masked["llm_api_key"])
    table.add_row("Pulse Recipient", masked["pulse_recipient"])
    table.add_row("Config Path", masked["config_path"])
    table.add_row("MCP Config Path", masked["mcp_config_path"])
    table.add_row("Data Dir", masked["data_dir"])
    table.add_row("Log Level", masked["log_level"])

    app_cfg = settings.app_config
    table.add_row("Ingest Window", f"{app_cfg.ingest.window_weeks} weeks")
    table.add_row("Play Store Enabled", str(app_cfg.sources.play_store.enabled))
    table.add_row("App Store Enabled", str(app_cfg.sources.app_store.enabled))
    table.add_row("Max Themes", str(app_cfg.theming.max_themes))
    table.add_row("Max Words", str(app_cfg.compose.max_words))
    table.add_row("Chat Model", app_cfg.llm.model_chat)
    table.add_row("Embedding Model", app_cfg.llm.model_embed)

    console.print(table)


@app.command()
def budget() -> None:
    """Show today's remembered Groq budget (runs and tokens used; resets 00:00 UTC)."""
    ledger = budget_from_settings(get_settings())
    if ledger is None:
        rprint("LLM budget tracking is disabled (llm.budget.enabled: false).")
        return
    st = ledger.status()
    table = Table(title=f"LLM budget — {st['day']} (UTC)")
    table.add_column("Item", style="cyan", no_wrap=True)
    table.add_column("Value", style="magenta")
    table.add_row("Runs", f"{st['runs_used']} / {st['runs_max']}")
    table.add_row("Tokens used", f"{st['tokens_used']:,} / {st['tokens_allowance']:,}")
    table.add_row("Tokens remaining", f"{st['tokens_remaining']:,}")
    table.add_row("Estimated tokens per run", f"{st['estimated_run_tokens']:,}")
    table.add_row("Blocked until", st["blocked_until"] or "—")
    console.print(table)


@app.command()
def fetch(
    source: Annotated[
        str,
        typer.Option("--source", "-s", help="Source to fetch: all | play_store | app_store"),
    ] = "all",
    weeks: Annotated[
        int | None,
        typer.Option("--weeks", "-w", help="Number of weeks to fetch (default: config value)"),
    ] = None,
) -> None:
    """Fetch reviews from Play Store and App Store (Phase 1)."""
    if source not in ("all", "play_store", "app_store"):
        raise typer.BadParameter("source must be one of: all, play_store, app_store")
    settings = get_settings()
    n_weeks = weeks or settings.app_config.ingest.window_weeks
    week_str = last_completed_week()
    start_d, end_d = window_bounds(week_str, n_weeks)

    rprint(f"Target week: [cyan]{week_str}[/cyan]")
    rprint(f"Window: [cyan]{start_d}[/cyan] -> [cyan]{end_d}[/cyan] ({n_weeks} weeks)")

    try:
        result = fetch_all(build_sources(settings.app_config, source), week_str, n_weeks)
    except AllSourcesFailedError as exc:
        for name, err in exc.errors.items():
            rprint(f"[red]{name} failed:[/red] {err}")
        raise typer.Exit(code=1) from exc

    by_source = result.counts_by_source()
    summary = Table(title="Reviews fetched (in memory only; nothing written to disk)")
    summary.add_column("Source", style="cyan")
    summary.add_column("Reviews", justify="right")
    summary.add_column("Coverage start")
    sources_to_display: tuple[Source, ...] = ("play_store", "app_store")
    for name in sources_to_display:
        if name in result.source_errors:
            summary.add_row(name, "error", result.source_errors[name])
        elif name in by_source or name in result.coverage_start:
            cov = result.coverage_start.get(name)
            summary.add_row(name, str(by_source.get(name, 0)), str(cov) if cov else "-")
    console.print(summary)

    per_week = Table(title="Reviews per ISO week")
    per_week.add_column("Week", style="cyan")
    per_week.add_column("play_store", justify="right")
    per_week.add_column("app_store", justify="right")
    for wk, counts in result.counts_by_week().items():
        per_week.add_row(wk, str(counts.get("play_store", 0)), str(counts.get("app_store", 0)))
    console.print(per_week)


@app.command()
def preprocess(
    source: Annotated[
        str,
        typer.Option("--source", "-s", help="Source to fetch: all | play_store | app_store"),
    ] = "all",
    week: Annotated[
        str,
        typer.Option("--week", help="Target ISO week, e.g. '2026-W42' or 'auto'"),
    ] = "auto",
    weeks: Annotated[
        int | None,
        typer.Option("--weeks", "-w", help="Number of weeks to fetch (default: config value)"),
    ] = None,
    no_embeddings: Annotated[
        bool,
        typer.Option("--no-embeddings", help="Skip embedding generation"),
    ] = False,
) -> None:
    """Preprocess reviews (Phase 2): filter (>8 words), scrub PII, pseudonymize, and persist."""
    if source not in ("all", "play_store", "app_store"):
        raise typer.BadParameter("source must be one of: all, play_store, app_store")

    settings = get_settings()
    n_weeks = weeks or settings.app_config.ingest.window_weeks
    week_str = last_completed_week() if week == "auto" else week
    start_d, end_d = window_bounds(week_str, n_weeks)

    rprint(f"Target week: [cyan]{week_str}[/cyan]")
    rprint(f"Window: [cyan]{start_d}[/cyan] -> [cyan]{end_d}[/cyan] ({n_weeks} weeks)")
    rprint(f"Filtering threshold: [yellow]> {settings.app_config.ingest.min_words} words[/yellow]")

    try:
        fetch_result = fetch_all(build_sources(settings.app_config, source), week_str, n_weeks)
    except AllSourcesFailedError as exc:
        for name, err in exc.errors.items():
            rprint(f"[red]{name} failed:[/red] {err}")
        raise typer.Exit(code=1) from exc

    rprint(f"Raw reviews fetched: [bold]{len(fetch_result.reviews)}[/bold]")

    run_store = RunStore(base_dir=settings.data_dir)
    llm_client = None
    if not no_embeddings:
        try:
            llm_client = get_llm_client()
        except Exception as exc:
            rprint(
                f"[yellow]Could not initialize full LLM client ({exc}), "
                "falling back to mock embedder[/yellow]"
            )
            from pulse.llm.client import MockLLMClient

            llm_client = MockLLMClient()

    clean_reviews = process_and_persist_reviews(
        raw_reviews=fetch_result.reviews,
        target_week=week_str,
        n_weeks=n_weeks,
        config=settings.app_config,
        run_store=run_store,
        llm_client=llm_client,
        coverage_start=fetch_result.coverage_start,
        compute_embeddings=not no_embeddings,
    )

    rprint(f"Clean reviews retained and saved: [bold green]{len(clean_reviews)}[/bold green]")
    artifact_loc = f"{settings.data_dir}/runs/{week_str}/reviews_clean.jsonl"
    rprint(f"Artifact location: [cyan]{artifact_loc}[/cyan]")

    pii_count = sum(len(r.pii_hits) for r in clean_reviews)
    rprint(f"PII entities scrubbed: [magenta]{pii_count}[/magenta]")


@app.command()
def theme(
    week: Annotated[
        str,
        typer.Option("--week", help="Target ISO week, e.g. '2026-W42' or 'auto'"),
    ] = "auto",
    source_weighting: Annotated[
        str | None,
        typer.Option("--source-weighting", help="Ranking mode: volume | balanced"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Run theming without persisting to disk"),
    ] = False,
    batch_size: Annotated[
        int | None,
        typer.Option("--batch-size", help="Batch size for review classification (5..100)"),
    ] = None,
) -> None:
    """Discover, classify, and rank product themes from cleaned reviews (Phase 3)."""
    settings = get_settings()
    week_str = last_completed_week() if week == "auto" else week
    weighting = source_weighting or settings.app_config.ranking.source_weighting
    if weighting not in ("volume", "balanced"):
        raise typer.BadParameter("source-weighting must be either 'volume' or 'balanced'")

    rprint(f"Target week: [cyan]{week_str}[/cyan]")
    rprint(f"Source weighting: [yellow]{weighting}[/yellow]")

    run_store = RunStore(base_dir=settings.data_dir)
    try:
        llm_client = get_llm_client()
    except Exception as exc:
        rprint(
            f"[yellow]Could not initialize full LLM client ({exc}), "
            "falling back to local embedder / centroid classifier[/yellow]"
        )
        from pulse.llm.client import MockLLMClient

        llm_client = MockLLMClient()

    themes, assignments = run_theming_pipeline(
        week=week_str,
        config=settings.app_config,
        run_store=run_store,
        llm_client=llm_client,
        source_weighting=weighting,
        batch_size=batch_size or settings.app_config.theming.batch_size,
        dry_run=dry_run,
    )

    table = Table(title=f"Discovered Themes - {week_str} ({weighting} weighting)")
    table.add_column("Rank", style="cyan", justify="right")
    table.add_column("ID", style="dim")
    table.add_column("Theme Name", style="bold")
    table.add_column("Reviews", justify="right")
    table.add_column("Share", justify="right")
    table.add_column("Avg Rating", justify="right")
    table.add_column("Low %", justify="right")
    table.add_column("Play", justify="right")
    table.add_column("iOS", justify="right")
    table.add_column("WoW", justify="right")

    for rank, t in enumerate(themes, start=1):
        wow = f"{t.wow_delta:+.1f} pts" if t.wow_delta is not None else "-"
        table.add_row(
            str(rank),
            t.theme_id,
            t.name,
            str(t.review_count),
            f"{t.share * 100:.1f}%",
            f"{t.avg_rating:.1f}",
            f"{t.low_rating_share * 100:.1f}%",
            str(t.by_source.get("play_store", 0)),
            str(t.by_source.get("app_store", 0)),
            wow,
        )

    console.print(table)
    rprint(f"Total reviews assigned: [bold green]{len(assignments)}[/bold green]")
    other_cnt = sum(1 for a in assignments if a.theme_id == "other")
    rprint(
        f"Unclassified ('other') count: [yellow]{other_cnt}[/yellow] ({other_cnt / len(assignments) * 100:.1f}%)"
    )


@app.command()
def compose(
    week: Annotated[
        str,
        typer.Option("--week", help="Target ISO week, e.g. '2026-W42' or 'auto'"),
    ] = "auto",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Run composition without saving artifacts to disk"),
    ] = False,
) -> None:
    """Compose executive weekly review pulse note with quotes, actions & guardrails (Phase 4)."""
    settings = get_settings()
    week_str = last_completed_week() if week == "auto" else week
    rprint(f"Target week: [cyan]{week_str}[/cyan]")
    rprint(f"Dry run: [yellow]{dry_run}[/yellow]")

    run_store = RunStore(base_dir=settings.data_dir)
    try:
        llm_client = get_llm_client()
    except Exception as exc:
        rprint(
            f"[yellow]Could not initialize full LLM client ({exc}), "
            "falling back to MockLLMClient / deterministic fallback[/yellow]"
        )
        from pulse.llm.client import MockLLMClient

        llm_client = MockLLMClient()

    try:
        pulse_obj, report = run_compose_pipeline(
            week=week_str,
            config=settings.app_config,
            run_store=run_store,
            llm_client=llm_client,
            dry_run=dry_run,
        )
    except Exception as exc:
        rprint(f"[bold red]Composition failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    rprint("\n[bold cyan]=== Rendered Pulse Document (pulse.md) ===[/bold cyan]\n")
    console.print(pulse_obj.markdown)
    rprint("[bold cyan]==========================================[/bold cyan]\n")

    table = Table(title=f"Guardrails Validation Report - {week_str}")
    table.add_column("Check", style="cyan")
    table.add_column("Type", style="dim")
    table.add_column("Status", justify="center")

    hard_checks = [
        "theme_count",
        "quote_count",
        "verbatim",
        "pii",
        "word_limit",
        "action_grounding",
        "no_identifiers",
        "source_attribution",
        "quote_rules",
    ]
    soft_checks = ["source_coverage", "tone"]

    for c in hard_checks:
        status_str = (
            "[bold green]PASS[/bold green]"
            if report.checks.get(c, False)
            else "[bold red]FAIL[/bold red]"
        )
        table.add_row(c, "Hard", status_str)

    for c in soft_checks:
        status_str = (
            "[bold green]PASS[/bold green]"
            if report.checks.get(c, False)
            else "[yellow]WARN[/yellow]"
        )
        table.add_row(c, "Soft", status_str)

    table.add_row(
        "body_word_budget", "Hard", f"{report.word_count} / {report.max_words_allowed} words"
    )
    console.print(table)

    if report.hard_failures:
        rprint("\n[bold red]Hard Failures Detected:[/bold red]")
        for fail in report.hard_failures:
            rprint(f"- [red]{fail}[/red]")

    if report.soft_warnings:
        rprint("\n[yellow]Soft Warnings:[/yellow]")
        for warn in report.soft_warnings:
            rprint(f"- [yellow]{warn}[/yellow]")

    if not report.passed:
        rprint("\n[bold red]Result: FAILED VALIDATION (Publishing blocked)[/bold red]")
        raise typer.Exit(code=1)
    else:
        rprint("\n[bold green]Result: ALL HARD CHECKS PASSED[/bold green]")
        if not dry_run:
            rprint(f"Saved artifacts to [cyan]{settings.data_dir}/runs/{week_str}/[/cyan]")


@app.command()
def run(
    week: Annotated[
        str,
        typer.Option("--week", help="Target ISO week, e.g. '2026-W42' or 'auto'"),
    ] = "auto",
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Generate pulse and run guardrails without live MCP publishing"
        ),
    ] = False,
    from_step: Annotated[
        str | None,
        typer.Option(
            "--from-step",
            help="Resume from step: ingest | preprocess | theming | compose | publish",
        ),
    ] = None,
    send: Annotated[
        bool,
        typer.Option("--send", help="Send email instead of drafting (when publishing)"),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Skip confirmation prompt when sending email"),
    ] = False,
    force_append: Annotated[
        bool,
        typer.Option("--force-append", help="Append even if this week was already appended"),
    ] = False,
    resend: Annotated[
        bool,
        typer.Option("--resend", help="Draft or send email even if already done"),
    ] = False,
    retention_weeks: Annotated[
        int,
        typer.Option("--retention-weeks", help="Retention window in weeks for historical data"),
    ] = 12,
    skip_retention: Annotated[
        bool,
        typer.Option("--skip-retention", help="Skip retention pruning after pipeline run"),
    ] = False,
    skip_health_check: Annotated[
        bool,
        typer.Option("--skip-health-check", help="Skip pre-flight MCP server /healthz check"),
    ] = False,
    batch_size: Annotated[
        int | None,
        typer.Option("--batch-size", help="Batch size for review classification (5..100)"),
    ] = None,
) -> None:
    """Run the end-to-end pulse generation pipeline (Phase 7)."""
    from pulse.orchestrator import LockError, PreflightError, run_pipeline

    settings = get_settings()
    target_week = last_completed_week() if week == "auto" else week

    rprint("[bold cyan]Groww Review Pulse — End-to-End Run[/bold cyan]")
    rprint(f"Target week: [cyan]{target_week}[/cyan]")
    rprint(f"Mode: [yellow]{'DRY RUN' if dry_run else 'LIVE'}[/yellow]")
    if from_step:
        rprint(f"Resume from step: [magenta]{from_step}[/magenta]")

    # Confirmation safeguard if send mode is requested
    if send and not dry_run and not yes:
        typer.confirm(
            f"This SENDS a real email to {settings.pulse_recipient} and cannot be undone. Continue?",
            abort=True,
        )

    try:
        result = run_pipeline(
            week=target_week,
            dry_run=dry_run,
            from_step=from_step,
            send=send,
            force_append=force_append,
            resend=resend,
            retention_weeks=retention_weeks,
            skip_retention=skip_retention,
            settings=settings,
            check_health=not skip_health_check,
            batch_size=batch_size,
        )
    except LockError as exc:
        rprint(f"\n[bold red]Lock Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    except PreflightError as exc:
        rprint(f"\n[bold red]Pre-flight Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    except BudgetExceededError as exc:
        rprint(f"\n[bold yellow]LLM budget:[/bold yellow] {exc}")
        rprint("Check remaining budget with [cyan]pulse budget[/cyan].")
        raise typer.Exit(code=3) from exc
    except Exception as exc:
        rprint(f"\n[bold red]Pipeline Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    manifest = result.manifest
    rprint("\n" + "=" * 60)
    rprint(f"[bold]Pipeline Summary — {manifest.run_id}[/bold]")
    rprint("=" * 60)

    # Status & timing table
    summary_table = Table(title="Execution Overview", box=box.ROUNDED)
    summary_table.add_column("Property", style="bold")
    summary_table.add_column("Value")

    status_color = "green" if manifest.status == "success" else "red"
    summary_table.add_row(
        "Status", f"[bold {status_color}]{manifest.status.upper()}[/bold {status_color}]"
    )
    summary_table.add_row("Target Week", manifest.week)
    summary_table.add_row("Total Wall Time", f"{result.step_timings.get('total', 0.0)}s")
    for step_name, dur in result.step_timings.items():
        if step_name != "total":
            summary_table.add_row(f"  Step: {step_name}", f"{dur}s")

    for k, v in manifest.counts.items():
        summary_table.add_row(f"Count: {k}", str(v))

    console.print(summary_table)

    # Guardrails table if validation ran
    if result.validation_report:
        rprint("\n[bold]Guardrails Verification:[/bold]")
        gr_table = Table(box=box.SIMPLE)
        gr_table.add_column("Check")
        gr_table.add_column("Status")
        for chk, passed in result.validation_report.checks.items():
            gr_table.add_row(
                chk,
                "[bold green]PASS[/bold green]" if passed else "[bold red]FAIL[/bold red]",
            )
        console.print(gr_table)

    # Publishing details
    if manifest.doc_url:
        rprint(
            f"\n[bold green]Google Doc:[/bold green] [link={manifest.doc_url}]{manifest.doc_url}[/link]"
        )
    if manifest.publish and manifest.publish.email:
        email_rec = manifest.publish.email
        rprint(
            f"[bold green]Email Delivery:[/bold green] mode=[cyan]{email_rec.mode}[/cyan], "
            f"status=[cyan]{email_rec.status}[/cyan], "
            f"draft_id=[cyan]{email_rec.draft_id}[/cyan], "
            f"message_id=[cyan]{email_rec.message_id}[/cyan]"
        )

    # Retention summary
    if result.pruned_items.get("pruned_runs") or result.pruned_items.get("pruned_snapshots"):
        rprint(
            f"[cyan]Retention Pruned:[/cyan] {len(result.pruned_items.get('pruned_runs', []))} run file(s), "
            f"{len(result.pruned_items.get('pruned_snapshots', []))} snapshot(s)."
        )

    if manifest.status != "success":
        raise typer.Exit(code=1)


@app.command()
def publish(
    week: Annotated[
        str,
        typer.Option("--week", help="ISO week of the saved pulse to publish, e.g. '2026-W42'"),
    ] = "auto",
    docs_only: Annotated[bool, typer.Option("--docs-only", help="Only append to the Doc")] = False,
    email_only: Annotated[
        bool, typer.Option("--email-only", help="Only draft/send the email")
    ] = False,
    send: Annotated[
        bool, typer.Option("--send", help="Send the email instead of drafting it")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the send confirmation")] = False,
    force_append: Annotated[
        bool, typer.Option("--force-append", help="Append even if this week was already appended")
    ] = False,
    resend: Annotated[bool, typer.Option("--resend", help="Draft/send the email again")] = False,
    doc_confirmed: Annotated[
        bool, typer.Option("--doc-confirmed", help="Mark an unknown-outcome append as done")
    ] = False,
    email_confirmed: Annotated[
        bool, typer.Option("--email-confirmed", help="Mark an unknown-outcome email as done")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Render only; make no MCP calls")
    ] = False,
) -> None:
    """Publish a saved pulse to Google Docs and Gmail (draft or send) via MCP (Phase 5)."""
    if docs_only and email_only:
        raise typer.BadParameter("--docs-only and --email-only are mutually exclusive")

    import asyncio

    from pulse.publish.errors import PublishError
    from pulse.publish.pipeline import PublishOptions, effective_email_mode, publish_pulse

    settings = get_settings()
    target_week = last_completed_week() if week == "auto" else week
    opts = PublishOptions(
        docs=not email_only,
        email=not docs_only,
        send=send,
        force_append=force_append,
        resend=resend,
        doc_confirmed=doc_confirmed,
        email_confirmed=email_confirmed,
        dry_run=dry_run,
    )
    mode = effective_email_mode(settings, opts)
    rprint(f"Publishing week: [cyan]{target_week}[/cyan] (email mode: [yellow]{mode}[/yellow])")

    if mode == "send" and opts.email and not dry_run and not yes:
        typer.confirm(
            f"This SENDS a real email to {settings.pulse_recipient} and cannot be undone. Continue?",
            abort=True,
        )

    try:
        outcome = asyncio.run(
            publish_pulse(settings, RunStore(base_dir=settings.data_dir), target_week, opts)
        )
    except PublishError as exc:
        rprint(f"[bold red]Publish failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    if outcome.status == "dry_run":
        console.print(outcome.message)
        return
    if outcome.status != "success":
        rprint(f"[bold red]{outcome.status}:[/bold red] {outcome.message}")
        raise typer.Exit(code=1)

    st = outcome.state
    rprint(f"Doc append: [green]{st.doc_append.status}[/green]  {st.doc_url or ''}")
    if opts.email:
        rprint(f"Email ({st.email.mode or mode}): [green]{st.email.status}[/green]")


@app.command("eval")
def run_eval(
    week: Annotated[
        str,
        typer.Option("--week", help="ISO week whose recorded run artifacts are checked, or 'auto'"),
    ] = "auto",
    suite: Annotated[
        str,
        typer.Option("--suite", help="all | compliance | quality | publish"),
    ] = "all",
    live: Annotated[
        bool,
        typer.Option("--live", help="Also re-run theming twice for the stability eval (LLM cost)"),
    ] = False,
    judge: Annotated[
        bool,
        typer.Option("--judge", help="Also run LLM-as-judge evals (LLM cost)"),
    ] = False,
    no_report: Annotated[
        bool, typer.Option("--no-report", help="Do not write evals/reports/<timestamp>.json")
    ] = False,
) -> None:
    """Run the offline evaluation suite against recorded artifacts and fixtures (Phase 6).

    Exit code: 0 all hard and quality targets met, 1 a target missed, 2 the harness failed.
    """
    if suite not in ("all", "compliance", "quality", "publish"):
        raise typer.BadParameter("suite must be one of: all, compliance, quality, publish")

    from pathlib import Path

    # `evals/` lives at the repo root (not in the installed package).
    root = str(Path.cwd())
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from evals.run_evals import HarnessError, run_suite, write_report
    except ImportError as exc:  # pragma: no cover - wrong working directory
        rprint(f"[bold red]Run `pulse eval` from the repository root:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    target_week = last_completed_week() if week == "auto" else week
    rprint(f"Eval week: [cyan]{target_week}[/cyan]  suite: [cyan]{suite}[/cyan]")
    try:
        results, report = run_suite(target_week, suite, live=live, judge=judge)
    except HarnessError as exc:
        rprint(f"[bold red]Eval harness failed:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    table = Table(title=f"pulse eval - {report['run_at']} - offline={report['offline']}")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Eval")
    table.add_column("Gate", style="dim")
    table.add_column("Value", overflow="fold")
    table.add_column("Target")
    table.add_column("Result", justify="center")
    for r in results:
        value = r.value if isinstance(r.value, str | int | float | bool) else str(r.value)
        status = (
            "[green]PASS[/green]"
            if r.passed
            else ("[red]FAIL[/red]" if r.gate in ("hard", "quality") else "[yellow]WARN[/yellow]")
        )
        table.add_row(r.id, r.name, r.gate, str(value), r.target, status)
    console.print(table)

    for r in results:
        warning = r.details.get("warning")
        if warning:
            rprint(f"[yellow]{r.id}: {warning}[/yellow]")

    sm = report["summary"]
    rprint(
        f"Hard: {sm['hard_passed']}/{sm['hard_total']} - Quality: "
        f"{sm['quality_passed']}/{sm['quality_total']} - Soft warnings: {sm['soft_warnings']} "
        f"- Exit code: {report['exit_code']}"
    )
    if not no_report:
        path = write_report(report)
        rprint(f"Report written to [cyan]{path}[/cyan]")
    raise typer.Exit(code=report["exit_code"])


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host", help="Bind address")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Bind port")] = 8000,
) -> None:
    """Start the local web API (needs the 'web' extra: fastapi, uvicorn)."""
    import uvicorn

    uvicorn.run("pulse.api:app", host=host, port=port)


if __name__ == "__main__":
    app()
