import { useEffect } from "react";
import { useStudio } from "../store";

export function Modal() {
  const { modal, closeModal } = useStudio();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeModal();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [closeModal]);

  if (!modal) return null;
  return (
    <div className="scrim show" onClick={(e) => e.target === e.currentTarget && closeModal()}>
      {modal}
    </div>
  );
}
