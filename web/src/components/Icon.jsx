export default function Icon({ name, sm = false, fill = false, style }) {
  return (
    <span
      className={`icon${sm ? " sm" : ""}`}
      style={fill ? { fontVariationSettings: '"FILL" 1', ...style } : style}
      aria-hidden="true"
    >
      {name}
    </span>
  );
}
