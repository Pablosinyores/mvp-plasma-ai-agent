import { useStudio } from "../store";

const accentVar: Record<string, string> = {
  blue: "var(--blue)",
  green: "var(--green)",
  red: "var(--red)",
  amber: "var(--amber)",
  violet: "var(--violet)",
};

export function Toasts() {
  const { toasts } = useStudio();
  return (
    <div id="toasts">
      {toasts.map((t) => (
        <div className="toast" key={t.id} style={{ ["--accent" as string]: accentVar[t.accent] }}>
          <div className="tt">{t.title}</div>
          {t.msg && <div className="tm">{t.msg}</div>}
        </div>
      ))}
    </div>
  );
}
