export default function HomePage() {
  return (
    <main style={{ fontFamily: "system-ui", padding: "2rem" }}>
      <h1>ChatVector Next.js proxy example</h1>
      <p>
        This app exposes <code>/api/*</code> routes only. Import the hooks from{" "}
        <code>../src</code> in your client components and point{" "}
        <code>ProxyClient</code> at this origin.
      </p>
    </main>
  );
}
