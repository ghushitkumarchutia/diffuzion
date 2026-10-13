import { Feedback } from "../components/feedback";

export default function Loading() {
  return (
    <main id="main-content" className="container">
      <Feedback kind="loading" title="Loading…" message="Please wait." />
    </main>
  );
}
