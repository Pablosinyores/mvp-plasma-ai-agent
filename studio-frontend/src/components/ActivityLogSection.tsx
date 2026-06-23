import { useEffect, useRef } from "react";
import { useStudio } from "../store";
import { SectionHead } from "./SectionHead";

export function ActivityLogSection() {
  const { logs } = useStudio();
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const box = boxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [logs]);

  return (
    <div className="section">
      <SectionHead title="activity log" />
      <div className="logbox">
        <div className="lh">
          <span className="dot on" style={{ width: 7, height: 7 }} /> studio&nbsp;<b>›</b>&nbsp;live
          operations &amp; settlement
        </div>
        <div className="log" ref={boxRef}>
          {logs.map((l) => (
            <div className="l" key={l.id}>
              <span className="t">{l.time}</span>{"  "}
              <span className={l.kind} dangerouslySetInnerHTML={{ __html: l.html }} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
