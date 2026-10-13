type FeedbackProps = {
  kind: "error" | "loading" | "empty";
  title: string;
  message?: string;
  requestId?: string;
};

export function Feedback({ kind, title, message, requestId }: FeedbackProps) {
  const safeRequestId = requestId && /^[A-Za-z0-9_-]{1,64}$/.test(requestId)
    ? requestId
    : undefined;
  return (
    <section
      className={`feedback feedback-${kind}`}
      role={kind === "error" ? "alert" : "status"}
      aria-live={kind === "error" ? "assertive" : "polite"}
      aria-atomic="true"
      aria-busy={kind === "loading"}
    >
      <h2>{title}</h2>
      {message && <p>{message}</p>}
      {safeRequestId && <p>Request reference: <code>{safeRequestId}</code></p>}
    </section>
  );
}
