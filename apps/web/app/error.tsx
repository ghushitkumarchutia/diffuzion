"use client";

import { Feedback } from "../components/feedback";

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main id="main-content" className="container">
      <Feedback
        kind="error"
        title="Something went wrong"
        message="Please try again."
        requestId={error.digest}
      />
      <button type="button" onClick={reset}>
        Try again
      </button>
    </main>
  );
}
