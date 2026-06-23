export function SectionHead({ title, count }: { title: string; count?: string }) {
  return (
    <div className="sec-head">
      <h2>{title}</h2>
      {count ? <span className="cnt">{count}</span> : null}
      <span className="ln" />
    </div>
  );
}
