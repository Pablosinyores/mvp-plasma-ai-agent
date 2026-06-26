// Dynamic section registry — each entry is its own page, reachable from the nav bar.
// To add a future page (e.g. "disputes", "model usage", "policies"), build a component that
// takes SectionProps and append one entry here with a unique `path`. The nav bar and the
// router pick it up automatically — no other file needs to change.
import type { FC } from "react";
import type { StudioState } from "../types";
import { ActivityLogSection } from "../components/ActivityLogSection";
import { AgentsSection } from "../components/AgentsSection";
import { FeedSecuritySection } from "../components/FeedSecuritySection";
import { JobsSection } from "../components/JobsSection";
import { OverviewSection } from "../components/OverviewSection";
import { PlaygroundSection } from "../components/PlaygroundSection";
import { Session7702Section } from "../components/Session7702Section";
import { StrategySection } from "../components/StrategySection";
import { VizSection } from "../components/VizSection";

export interface SectionProps {
  state: StudioState;
}

export interface SectionDef {
  id: string;
  /** Hash route fragment, e.g. "agents" → #/agents */
  path: string;
  /** Nav bar label */
  label: string;
  /** Sub-title shown in the page header under the label */
  blurb: string;
  Component: FC<SectionProps>;
}

export const SECTIONS: SectionDef[] = [
  { id: "overview", path: "overview", label: "Overview", blurb: "Studio at a glance", Component: OverviewSection },
  { id: "agents", path: "agents", label: "Agents", blurb: "On-chain identities, balances & actions", Component: AgentsSection },
  { id: "playground", path: "playground", label: "Playground", blurb: "Drive agents interactively", Component: PlaygroundSection },
  { id: "strategy", path: "strategy", label: "Strategy", blurb: "Standing trading strategies & live ticks", Component: StrategySection },
  { id: "my-wallet", path: "my-wallet", label: "My Wallet", blurb: "Trade from your own address (EIP-7702)", Component: Session7702Section },
  { id: "jobs", path: "jobs", label: "Jobs", blurb: "Funded work and settlement", Component: JobsSection },
  { id: "analytics", path: "analytics", label: "Analytics", blurb: "Spend, earnings & flow", Component: VizSection },
  { id: "feed-security", path: "security", label: "Security", blurb: "Guards, caps & injection drills", Component: FeedSecuritySection },
  { id: "activity-log", path: "activity", label: "Activity", blurb: "Live event log", Component: ActivityLogSection },
];

export const DEFAULT_PATH = SECTIONS[0].path;
