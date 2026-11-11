import { createContext, useContext } from "react";
import { request } from "./api";
export interface ParticipantMode {
  preview: boolean;
  stale: boolean;
  home: string;
  room: (slug: string) => string;
  request: typeof request;
}
const ordinary: ParticipantMode = {
  preview: false,
  stale: false,
  home: "/quest",
  room: (slug) => `/quest/rooms/${encodeURIComponent(slug)}`,
  request,
};
export const ParticipantModeContext = createContext<ParticipantMode>(ordinary);
export function useParticipantMode() {
  return useContext(ParticipantModeContext);
}
