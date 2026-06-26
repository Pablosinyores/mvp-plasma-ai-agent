import { SECTIONS } from "../sections/registry";

interface Props {
  active: string;
  onNavigate: (path: string) => void;
}

export function NavBar({ active, onNavigate }: Props) {
  return (
    <nav className="nav">
      {SECTIONS.map(({ id, path, label }) => (
        <a
          key={id}
          href={`#/${path}`}
          className={`nav-link ${active === path ? "on" : ""}`}
          onClick={(e) => {
            e.preventDefault();
            onNavigate(path);
          }}
        >
          {label}
        </a>
      ))}
    </nav>
  );
}
