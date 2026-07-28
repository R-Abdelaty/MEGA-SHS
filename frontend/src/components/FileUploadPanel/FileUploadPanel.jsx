import { useRef, useState } from "react";
import Button from "../ui/Button";
import CustomSelect from "../ui/CustomSelect";
import uploadIcon from "../../../icons/upload.svg";
import "./FileUploadPanel.css";

const labels = [
  "Current Schedule",
  "Lecturer Availability",
  "Room Availability",
  "Student Groups",
  "University Rules",
  "Exam Schedule",
  "Equipment",
  "Course Enrollment",
  "Other",
];

const acceptedTypes = ".pdf,.xls,.xlsx";
const allowedFilePattern = /\.(pdf|xls|xlsx)$/i;

function formatFileSize(size) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export default function FileUploadPanel({ files, onAddFile, onRemoveFile }) {
  const inputRef = useRef(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [label, setLabel] = useState("");
  const [error, setError] = useState("");
  const [isDragging, setIsDragging] = useState(false);

  function chooseFile(file) {
    if (!file) return;
    if (!allowedFilePattern.test(file.name)) {
      setSelectedFile(null);
      setError("Only PDF, XLS, or XLSX files are supported.");
      return;
    }
    setSelectedFile(file);
    setError("");
  }

  function handleSubmit(event) {
    event.preventDefault();
    if (!selectedFile || !label) {
      setError("Choose a file and assign a label before adding it.");
      return;
    }

    onAddFile({
      id: `${selectedFile.name}-${selectedFile.lastModified}-${Date.now()}`,
      name: selectedFile.name,
      size: selectedFile.size,
      label,
    });
    setSelectedFile(null);
    setLabel("");
    setError("");
    event.currentTarget.reset();
  }

  function handleDrop(event) {
    event.preventDefault();
    setIsDragging(false);
    chooseFile(event.dataTransfer.files[0]);
  }

  return (
    <section className="side-panel upload-panel" aria-label="Schedule Inputs">
      <form className="upload-form" onSubmit={handleSubmit}>
        <button
          type="button"
          className={`drop-zone ${isDragging ? "drop-zone--active" : ""}`}
          onClick={() => inputRef.current?.click()}
          onDragEnter={(event) => {
            event.preventDefault();
            setIsDragging(true);
          }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={() => setIsDragging(false)}
          onDrop={handleDrop}
          aria-label="Choose a PDF or Excel schedule file"
        >
          <span className="drop-zone__title">Drop file</span>
          <img
            className="drop-zone__upload"
            src={uploadIcon}
            alt=""
            aria-hidden="true"
          />
          <span className="drop-zone__formats">PDF · XLS · XLSX</span>
        </button>
        <input
          className="sr-only"
          ref={inputRef}
          type="file"
          accept={acceptedTypes}
          onChange={(event) => chooseFile(event.target.files[0])}
        />

        {selectedFile ? (
          <div className="selected-file" aria-live="polite">
            {selectedFile.name}
          </div>
        ) : null}

        <span className="field-label" id="file-label-label">
          Type
        </span>
        <div className="upload-form__label-row">
          <CustomSelect
            id="file-label"
            value={label}
            options={labels}
            placeholder="Select a type"
            ariaLabelledBy="file-label-label"
            onChange={(nextLabel) => {
              setLabel(nextLabel);
              setError("");
            }}
          />
          <Button
            className="upload-form__add"
            type="submit"
            variant="accent"
          >
            <span aria-hidden="true">+</span> Add
          </Button>
        </div>

        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}
      </form>

      <div className="file-list">
        <h3>Added schedules</h3>
        {files.length === 0 ? (
          <p className="file-list__empty">No files added yet.</p>
        ) : (
          <ul>
            {files.map((file) => (
              <li className="file-row" key={file.id}>
                <div className="file-row__details">
                  <strong>{file.label}</strong>
                  <span title={file.name}>{file.name}</span>
                  <small>{formatFileSize(file.size)}</small>
                </div>
                <Button
                  className="file-row__remove"
                  type="button"
                  onClick={() => onRemoveFile(file.id)}
                  aria-label={`Remove ${file.name}`}
                >
                  <span aria-hidden="true">×</span>
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
