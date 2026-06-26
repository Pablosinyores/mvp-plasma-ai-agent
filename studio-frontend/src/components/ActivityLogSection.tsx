import { useEffect, useRef } from "react";
import { useStudio } from "../store";
import { JobDetailModal } from "./modals/JobDetailModal";
import { SectionHead } from "./SectionHead";

export function ActivityLogSection() {
  const { logs, openModal, closeModal } = useStudio();
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
            <div
              className={`l ${l.jobId != null ? "clickable" : ""}`}
              key={l.id}
              onClick={
                l.jobId != null
                  ? () => openModal(<JobDetailModal jobId={l.jobId!} onClose={closeModal} />)
                  : undefined
              }
              title={l.jobId != null ? `open job #${l.jobId}` : undefined}
            >
              <span className="t">{l.time}</span>{"  "}
              <span className={l.kind} dangerouslySetInnerHTML={{ __html: l.html }} />
              {l.jobId != null && <span className="l-go">↗</span>}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
