import CodeBlock from "../CodeBlock";

const DEV_POINTS = [
  {
    title: "Deploy anywhere",
    desc: "Run as a Docker container on your laptop, your server, or any cloud VM.",
  },
  {
    title: "Integrate via HTTP",
    desc: "Use the Python SDK or call the REST API directly from any language.",
  },
  {
    title: "Type-safe Python SDK",
    desc: "Full type hints. IDE autocomplete works out of the box.",
  },
  {
    title: "Community-first",
    desc: "MIT licensed. PRs welcome. Good first issues available.",
  },
];

export default function Developers() {
  const codeLines = [
    { parts: [{ type: "comment", text: "# Upload and wait for processing" }] },
    {
      parts: [
        { type: "default", text: "doc = cv." },
        { type: "function", text: "upload_document" },
        { type: "default", text: "(" },
        { type: "string", text: '"report.pdf"' },
        { type: "default", text: ")" },
      ],
    },
    {
      parts: [
        { type: "default", text: "cv." },
        { type: "function", text: "wait_for_ready" },
        { type: "default", text: "(doc.document_id)" },
      ],
    },
    { parts: [] },
    {
      parts: [
        { type: "comment", text: "# Ask questions against the document" },
      ],
    },
    {
      parts: [
        { type: "default", text: "answer = cv." },
        { type: "function", text: "chat" },
        { type: "default", text: "(" },
      ],
    },
    {
      parts: [
        { type: "default", text: "  " },
        { type: "string", text: '"Summarise the key findings."' },
        { type: "default", text: "," },
      ],
    },
    { parts: [{ type: "default", text: "  doc.document_id" }] },
    { parts: [{ type: "default", text: ")" }] },
    { parts: [] },
    {
      parts: [
        { type: "comment", text: "# Full response with source citations" },
      ],
    },
    { parts: [{ type: "default", text: "print(answer.answer)" }] },
    { parts: [{ type: "default", text: "print(answer.sources)" }] },
  ];
  return (
    <section id="developers" className="bg-background px-8 py-24">
      <div className="mx-auto max-w-[1100px]">
        <p className="mb-4 font-mono text-[0.78rem] uppercase tracking-[2px] text-accent">
          {"// built for developers"}
        </p>
        <h2 className="mb-4 text-[clamp(1.8rem,3.5vw,2.8rem)] font-semibold leading-tight tracking-[-0.8px] text-foreground">
          Designed for people who
          <br />
          read the source code.
        </h2>
        <p className="mb-12 max-w-[540px] text-[1.1rem] font-light leading-[1.7] text-muted">
          Spin up an instance, point the SDK at it, and start querying your
          documents over HTTP. No magic, no lock-in — just a clean API you can
          read and trust.
        </p>

        <div className="grid grid-cols-1 items-center gap-12 md:grid-cols-2">
          <div className="flex flex-col gap-4">
            {DEV_POINTS.map((p) => (
              <div
                key={p.title}
                className="flex items-start gap-3.5 rounded-r-[10px] border border-border border-l-[3px] border-l-accent bg-surface py-4 pl-5 pr-4"
              >
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                  className="mt-0.5 shrink-0 text-accent"
                >
                  <polyline points="20 6 9 17 4 12" />
                </svg>
                <div>
                  <h4 className="mb-0.5 text-[0.92rem] font-medium text-foreground">
                    {p.title}
                  </h4>
                  <p className="m-0 text-[0.9rem] text-muted">{p.desc}</p>
                </div>
              </div>
            ))}
          </div>
          <CodeBlock language="python" filename="upload_and_chat.py">
            {codeLines.map((line, i) => (
              <div key={i}>
                {line.parts.map((p, j) => (
                  <span
                    key={j}
                    style={{
                      color: `var(--syntax-${p.type}, var(--syntax-default))`,
                    }}
                  >
                    {p.text}
                  </span>
                ))}
              </div>
            ))}
          </CodeBlock>
        </div>
      </div>
    </section>
  );
}
