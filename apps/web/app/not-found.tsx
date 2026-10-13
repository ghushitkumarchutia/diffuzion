import Link from "next/link";

export default function NotFound() {
  return (
    <main id="main-content" className="container">
      <h1>Page unavailable</h1>
      <p>The requested page could not be found or is inaccessible.</p>
      <Link href="/">Return to Diffuzion</Link>
    </main>
  );
}
