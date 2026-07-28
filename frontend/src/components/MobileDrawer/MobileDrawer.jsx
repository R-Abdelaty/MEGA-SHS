import Button from "../ui/Button";
import "./MobileDrawer.css";

export default function MobileDrawer({ title, isOpen, onClose, children }) {
  if (!isOpen) return null;

  return (
    <div
      className="drawer-layer"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <aside
        className="mobile-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="mobile-drawer__header">
          <strong>{title}</strong>
          <Button onClick={onClose} aria-label={`Close ${title}`}>
            Close
          </Button>
        </div>
        <div className="mobile-drawer__content">{children}</div>
      </aside>
    </div>
  );
}
