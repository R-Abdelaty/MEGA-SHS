import { useEffect, useRef, useState } from "react";
import "./CustomSelect.css";

export default function CustomSelect({
  id,
  value,
  options,
  placeholder,
  ariaLabelledBy,
  onChange,
}) {
  const rootRef = useRef(null);
  const [isOpen, setIsOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(0);

  const selectedIndex = options.indexOf(value);
  const displayedValue = value || placeholder;

  useEffect(() => {
    if (!isOpen) return undefined;

    function handleOutsidePointer(event) {
      if (!rootRef.current?.contains(event.target)) {
        setIsOpen(false);
      }
    }

    document.addEventListener("pointerdown", handleOutsidePointer);
    return () =>
      document.removeEventListener("pointerdown", handleOutsidePointer);
  }, [isOpen]);

  function openMenu() {
    setHighlightedIndex(selectedIndex >= 0 ? selectedIndex : 0);
    setIsOpen(true);
  }

  function selectOption(option) {
    onChange(option);
    setIsOpen(false);
  }

  function handleKeyDown(event) {
    if (event.key === "Escape") {
      setIsOpen(false);
      return;
    }

    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!isOpen) {
        openMenu();
        return;
      }

      const direction = event.key === "ArrowDown" ? 1 : -1;
      setHighlightedIndex(
        (current) => (current + direction + options.length) % options.length,
      );
      return;
    }

    if (event.key === "Home" && isOpen) {
      event.preventDefault();
      setHighlightedIndex(0);
      return;
    }

    if (event.key === "End" && isOpen) {
      event.preventDefault();
      setHighlightedIndex(options.length - 1);
      return;
    }

    if ((event.key === "Enter" || event.key === " ") && isOpen) {
      event.preventDefault();
      selectOption(options[highlightedIndex]);
    }
  }

  return (
    <div className="custom-select" ref={rootRef}>
      <button
        id={id}
        className={`custom-select__trigger ${
          value ? "" : "custom-select__trigger--placeholder"
        }`}
        type="button"
        role="combobox"
        aria-labelledby={`${ariaLabelledBy} ${id}`}
        aria-controls={`${id}-listbox`}
        aria-expanded={isOpen}
        aria-haspopup="listbox"
        aria-activedescendant={
          isOpen ? `${id}-option-${highlightedIndex}` : undefined
        }
        onClick={() => {
          if (isOpen) setIsOpen(false);
          else openMenu();
        }}
        onKeyDown={handleKeyDown}
      >
        <span>{displayedValue}</span>
        <svg
          className="custom-select__chevron"
          aria-hidden="true"
          viewBox="0 0 20 20"
          width="18"
          height="18"
          fill="none"
        >
          <path
            d="M5.5 7.5 10 12l4.5-4.5"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      {isOpen ? (
        <ul
          className="custom-select__menu"
          id={`${id}-listbox`}
          role="listbox"
          aria-labelledby={ariaLabelledBy}
        >
          {options.map((option, index) => (
            <li
              className={`custom-select__option ${
                highlightedIndex === index
                  ? "custom-select__option--highlighted"
                  : ""
              } ${
                value === option ? "custom-select__option--selected" : ""
              }`}
              id={`${id}-option-${index}`}
              role="option"
              aria-selected={value === option}
              onMouseEnter={() => setHighlightedIndex(index)}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => selectOption(option)}
              key={option}
            >
              {option}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
