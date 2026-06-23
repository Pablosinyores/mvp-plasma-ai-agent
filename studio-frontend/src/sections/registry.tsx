// Dynamic section registry — the page renders these in order.
// To add a future section (e.g. "disputes", "model usage", "policies"), build a component that
// takes SectionProps and append one entry here. No other file needs to change.
import type { FC } from "react";
import type { StudioState } from "../types";
import { ActivityLogSection } from "../components/ActivityLogSection";
import { AgentsSection } from "../components/AgentsSection";
import { FeedSecuritySection } from "../components/FeedSecuritySection";
import { JobsSection } from "../components/JobsSection";
import { PlaygroundSection } from "../components/PlaygroundSection";
import { VizSection } from "../components/VizSection";

export interface SectionProps {
  state: StudioState;
}

export interface SectionDef {
  id: string;
  Component: FC<SectionProps>;
}

export const SECTIONS: SectionDef[] = [
  { id: "agents", Component: AgentsSection },
  { id: "playground", Component: PlaygroundSection },
  { id: "jobs", Component: JobsSection },
  { id: "analytics", Component: VizSection },
  { id: "feed-security", Component: FeedSecuritySection },
  { id: "activity-log", Component: ActivityLogSection },
];
