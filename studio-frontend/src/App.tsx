import { useLiveState } from "./api/useLiveState";
import { DemoTour } from "./components/DemoTour";
import { Modal } from "./components/Modal";
import { Stats } from "./components/Stats";
import { Toasts } from "./components/Toasts";
import { TopBar } from "./components/TopBar";
import { SECTIONS } from "./sections/registry";

export default function App() {
  const { state, connected } = useLiveState();

  return (
    <>
      <div className="wrap">
        <TopBar chain={state.chain} connected={connected} />
        <Stats stats={state.stats} />
        {SECTIONS.map(({ id, Component }) => (
          <Component key={id} state={state} />
        ))}
      </div>
      <Modal />
      <Toasts />
      <DemoTour />
    </>
  );
}
