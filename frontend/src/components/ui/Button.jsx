export default function Button({
  variant = "secondary",
  className = "",
  children,
  ...props
}) {
  return (
    <button
      className={`button button--${variant} ${className}`.trim()}
      {...props}
    >
      {children}
    </button>
  );
}
