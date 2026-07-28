import "./ChangeSummaryPanel.css";

function formatTimestamp(value) {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return "";
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(timestamp);
}

export default function ChangeSummaryPanel({
  changeHistory,
  isLoading = false,
  error = null,
}) {
  return (
    <section
      className="side-panel change-panel"
      aria-labelledby="changes-heading"
    >
      <div className="side-panel__heading-row">
        <h2 id="changes-heading">Changes</h2>
      </div>

      <div className="change-feed">
        {isLoading ? (
          <p className="change-feed__state" role="status">
            Loading approved changes…
          </p>
        ) : null}
        {!isLoading && error ? (
          <p className="change-feed__state change-feed__state--error" role="alert">
            {error.message}
          </p>
        ) : null}
        {!isLoading && !error && changeHistory.length === 0 ? (
          <p className="change-feed__state">
            Approved healing runs will appear here.
          </p>
        ) : null}
        {!isLoading && !error
          ? changeHistory.map((group) => (
              <article
                className="change-group"
                aria-label={`Changes from ${formatTimestamp(group.timestamp)}`}
                key={group.run_id}
              >
                <time
                  className="change-group__time"
                  dateTime={group.timestamp}
                >
                  {formatTimestamp(group.timestamp)}
                </time>
                {group.summary ? (
                  <p className="change-group__summary">{group.summary}</p>
                ) : null}
                <ul>
                  {group.changes.map((change) => (
                    <li
                      aria-label={`${change.display.title}. ${change.display.detail}`}
                      key={change.action_id}
                    >
                      <div className="change-action__heading">
                        <strong>{change.display.title}</strong>
                      </div>
                      {change.display.status_label ||
                      change.display.detail ? (
                        <span className="change-action__detail">
                          {change.display.status_label ? (
                            <span className="change-action__status">
                              {change.display.status_label}
                            </span>
                          ) : null}
                          {change.display.status_label &&
                          change.display.detail
                            ? " · "
                            : null}
                          {change.display.detail}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </article>
            ))
          : null}
      </div>
    </section>
  );
}
