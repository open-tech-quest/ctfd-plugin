export type Phase = "draft" | "live" | "paused" | "closed" | "published";
export interface QuestEvent {
  name: string;
  status: Phase;
  start_at: string | null;
  end_at: string | null;
  paused_at: string | null;
  rosters_release_at: string | null;
  room_clock: "explicit" | "event";
  overall_clock: "event" | "team";
  server_now: string;
  results_published: boolean;
}
export type UserRole = "guest" | "participant" | "maintainer" | "administrator";
export interface Viewer {
  role: UserRole;
  can_manage_rooms: boolean;
  can_manage_users: boolean;
  authenticated: boolean;
  id: number | null;
  name: string | null;
  team_id: number | null;
  team_name: string | null;
  admin: boolean;
}
export interface Step {
  id: number;
  title: string;
  value: number;
  position: number;
  state: "locked" | "available" | "solved";
  solved_at: string | null;
}
export interface Score {
  points: number;
  gross_points: number;
  hint_costs: number;
}
export interface Progress extends Score {
  used_hint_ids: number[];
  used_hint_costs: Record<number, number>;
  available_points: number;
  credited_solved: number;
  score_elapsed_ms: number | null;
  solved: number;
  total: number;
  started_at: string | null;
  elapsed_ms: number | null;
  milestone_elapsed_ms: number | null;
  status: "not_started" | "active" | "complete";
}
export interface Room {
  pause?: RoomPause;
  revision?: string;
  can_delete?: boolean;
  delete_reason?: string | null;
  can_configure_topology?: boolean;
  topology_reason?: string | null;
  id: number;
  slug: string;
  name: string;
  subtitle: string;
  description: string;
  position: number;
  theme: {
    accent?: string;
    logo_url?: string;
    hero_url?: string;
    layout?: string;
  };
  steps: Step[];
  progress: Progress;
  team_score: Score;
  startable: boolean;
}
export interface RoomPause {
  paused: boolean;
  started_at: string | null;
  reason: string | null;
  revision: string;
}
export interface RegistrationState {
  enabled: boolean;
}
export interface QuestSync {
  csrfNonce: string | null;
  registration: RegistrationState;
  event: QuestEvent;
  viewer: Viewer;
  onboarding: Onboarding | null;
  revisions: { content: string; roster: string; progress: string };
}
export interface Bootstrap extends QuestSync {
  rooms: Room[];
}
export interface RoomProgress extends Partial<Score> {
  pause?: RoomPause;
  room_id: number;
  solved: number;
  path_solved?: number;
  solved_steps?: number[];
  total: number;
  milestone_elapsed_ms: number | null;
  rank: number | null;
  complete: boolean;
}
export interface StandingTeam extends Partial<Score> {
  id: number;
  name: string;
  rank: number | null;
  total: number;
  overall_elapsed_ms: number | null;
  room_progress: RoomProgress[];
}
export interface Standings {
  scoring_version?: number;
  event: QuestEvent;
  rooms: (Pick<Room, "id" | "slug" | "name"> & {
    pause?: RoomPause;
    total: number;
    available_points?: number;
    steps?: { id: number; value: number }[];
  })[];
  teams: StandingTeam[];
  room_leaders: Record<string, StandingTeam[]>;
  as_of: string;
}
export interface Projector {
  view: "overview" | "matrix" | "room" | "overall" | "awards";
  room_slug: string | null;
  server_now?: string;
  rotation?: {
    enabled: boolean;
    anchor_at: string | null;
    paused_at: string | null;
    overview_seconds: number;
    room_seconds: number;
    overall_seconds: number;
  };
}
export interface Team {
  hidden: boolean;
  banned: boolean;
  id: number;
  name: string;
  user_ids: number[];
  excluded: boolean;
  name_locked: boolean;
  ready_count: number;
}
export interface Participant {
  id: number;
  name: string;
  email: string;
  team_id: number | null;
  ready_at: string | null;
  late: boolean;
  banned: boolean;
}
export interface AdminState {
  registration: RegistrationState;
  onboarding: AttendanceState;
  demo: DemoContentState;
  event: QuestEvent;
  rooms: Room[];
  available_challenges: {
    id: number;
    name: string;
    state: string;
    room_slug: string | null;
  }[];
  teams: Team[];
  participants: Participant[];
  audit: {
    id: number;
    actor_id: number;
    action: string;
    detail: unknown;
    created_at: string;
  }[];
  standings: Standings;
  projector: Projector;
}
export interface DemoContentState {
  active: boolean;
  challenge_count: number;
  can_load: boolean;
  can_unload: boolean;
  reason: string | null;
}
export interface Challenge {
  id: number;
  name: string;
  value: number;
  description: string;
  files: string[];
  hints: {
    id: number;
    cost: number;
    title?: string | null;
    content?: string;
  }[];
  max_attempts?: number;
  attempts?: number;
  connection_info?: string;
}
declare global {
  interface Window {
    QUEST_INIT?: { csrfNonce?: string; viewer?: Viewer };
  }
}

export type OnboardingPhase = "check_in" | "closed" | "naming" | "finalized";
export interface AttendanceWindow {
  late_only: boolean;
  enabled: boolean;
  generation: number;
  opened_at: string | null;
  closed_at: string | null;
  can_open: boolean;
  reason: string | null;
}
export interface Onboarding {
  allow_team_naming: boolean;
  attendance: AttendanceWindow;
  phase: OnboardingPhase;
  team_size: number;
  ready_at: string | null;
  late: boolean;
  roster_released: boolean;
  team: {
    id: number;
    name: string;
    name_locked: boolean;
    members: { id: number; name: string; ready_at: string | null }[];
  } | null;
  can_ready: boolean;
  can_name: boolean;
}
export interface AttendanceState {
  allow_team_naming: boolean;
  late_admission: {
    allowed: boolean;
    reason: string | null;
    fingerprint: string | null;
  };
  attendance: AttendanceWindow;
  phase: OnboardingPhase;
  team_size: number;
  ready_count: number;
  pending_count: number;
  unassigned_ready_count: number;
  unnamed_count: number;
  can_start: boolean;
  start_reason: string | null;
}
export interface AttendancePlan {
  fingerprint: string;
  groups: { team_id: number | null; name: string; user_ids: number[] }[];
  moves: { user_id: number; from_team_id: number | null; to_group: number }[];
  unassigned_user_ids: number[];
  warnings: string[];
}

export interface ManagedUser {
  identity: string;
  can_delete: boolean;
  delete_reason: string | null;
  can_set_active: boolean;
  access_reason: string | null;
  id: number;
  name: string;
  email: string;
  role: Exclude<UserRole, "guest">;
  banned: boolean;
  team_id: number | null;
  team_name: string | null;
  ready_at: string | null;
  can_change_role: boolean;
  role_change_reason: string | null;
}
export interface ManagedRooms {
  revision?: string;
  event: QuestEvent;
  rooms: Room[];
  available_challenges: AdminState["available_challenges"];
}
export interface EditableFlag {
  content: string;
  case_insensitive: boolean;
}
export interface EditableHint {
  id: number;
  title: string;
  content: string;
  cost: number;
  prerequisites: number[];
}
export interface EditableChallenge {
  id: number;
  name: string;
  value: number;
  description: string;
  connection_info?: string;
  revision?: string;
  state: "hidden" | "visible" | "locked";
  type: string;
  source_key?: string | null;
  flags: EditableFlag[];
  hints: EditableHint[];
  can_edit: boolean;
  edit_reason: string | null;
  can_delete: boolean;
  delete_reason: string | null;
}
export interface ChallengeWorkspace {
  revision?: string;
  challenges: EditableChallenge[];
  can_create: boolean;
  create_reason: string | null;
}
