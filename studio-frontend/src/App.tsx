import { useLiveState } from "./api/useLiveState";
import { DemoTour } from "./components/DemoTour";
import { Modal } from "./components/Modal";
import { NavBar } from "./components/NavBar";
import { Toasts } from "./components/Toasts";
import { TopBar } from "./components/TopBar";
import { useHashRoute } from "./hooks/useHashRoute";
import { DEFAULT_PATH, SECTIONS } from "./sections/registry";

export default function App() {
  const { state, connected } = useLiveState();
  const [path, navigate] = useHashRoute(DEFAULT_PATH);

  const active = SECTIONS.find((s) => s.path === path) ?? SECTIONS[0];
  const { Component } = active;

  return (
    <>
      <div className="wrap">
        <TopBar chain={state.chain} connected={connected} />
        <NavBar active={active.path} onNavigate={navigate} />

        <div className="page" key={active.id}>
          {active.path !== "overview" && (
            <header className="page-head">
              <h1>{active.label}</h1>
              <p>{active.blurb}</p>
            </header>
          )}
          <Component state={state} />
        </div>
      </div>
      <Modal />
      <Toasts />
      <DemoTour />
    </>
  );
}
