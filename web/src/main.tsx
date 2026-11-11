import { decode } from "./api";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { usePath } from "./hooks";
import { useQuestSession } from "./use-quest-session";
import { viewerIdentity } from "./quest-session";
import { Link, Notice } from "./ui";
import { Lobby, RoomPage } from "./participant";
import { StandingsPage } from "./standings";
import { Stage } from "./stage";
import { Admin } from "./admin";
import { StaffHome } from "./staff-home";
import { ParticipantPreview } from "./preview";
import { isPreviewPath, previewHome, previewSlug } from "./preview-routing";
import { ParticipantSession } from "./participant-session";
import "./style.css";
const branding = "/plugins/open_tech_quest/assets/branding";
const pluginVersion = import.meta.env.VITE_PLUGIN_VERSION || "dev";
const fonts = document.createElement("link");
fonts.rel = "stylesheet";
fonts.href = `${branding}/fonts.css`;
document.head.appendChild(fonts);
function App() {
  const path = usePath();
  const bootstrap = useQuestSession(path);
  const data = bootstrap.data;
  const staff =
    data?.viewer.role === "maintainer" || data?.viewer.role === "administrator";
  const participantPreview = isPreviewPath(path);
  const roomPreviewSlug = previewSlug(path);
  const stage = path.replace(/\/$/, "") === "/quest/stage";
  return (
    <ParticipantSession
      key={bootstrap.live ? viewerIdentity(bootstrap.live.viewer) : "loading"}
      viewer={bootstrap.live?.viewer}
      onboarding={bootstrap.live?.onboarding ?? null}
      loading={bootstrap.loading}
      syncError={bootstrap.syncError}
      onChanged={bootstrap.refresh}
    >
      {stage ? (
        <Stage viewer={bootstrap.live?.viewer} />
      ) : (
        <>
          <a className="skip-link" href="#main">
            Skip to content
          </a>
          <header className="masthead">
            <div className="masthead-inner">
              <Link
                href={participantPreview ? previewHome : "/quest"}
                className="brand"
                aria-label="Open Tech Quest home"
              >
                <img src={`${branding}/quest-mark.png`} alt="Open Tech Quest" />
              </Link>
              <div className="event-label">
                Red Hat Hands-On Day<span>Darmstadt</span>
              </div>
              <nav aria-label="Main navigation">
                {participantPreview ? (
                  <Link href={previewHome} className="active">
                    Preview rooms
                  </Link>
                ) : (
                  <>
                    <Link
                      href="/quest"
                      className={
                        path === "/quest" ||
                        path === "/quest/" ||
                        path.includes("/rooms/")
                          ? "active"
                          : ""
                      }
                    >
                      {staff ? "Quest home" : "The rooms"}
                    </Link>
                    <Link
                      href="/quest/standings"
                      className={path.includes("standings") ? "active" : ""}
                    >
                      Progress & standings
                    </Link>
                    {data?.viewer.can_manage_rooms && (
                      <Link
                        href="/quest/admin"
                        className={path.includes("admin") ? "active" : ""}
                      >
                        Organize
                      </Link>
                    )}
                  </>
                )}
              </nav>
              <div className="account-nav">
                {data?.viewer.authenticated ? (
                  <>
                    <span>{data.viewer.team_name || data.viewer.name}</span>
                    <a href="/settings">Account</a>
                  </>
                ) : (
                  <>
                    <a href="/login?next=/quest">Sign in</a>
                    {data?.registration?.enabled && (
                      <a className="register-link" href="/register?next=/quest">
                        Register
                      </a>
                    )}
                  </>
                )}
              </div>
            </div>
          </header>
          <main id="main" className="app-main">
            {!data ? (
              bootstrap.error ? (
                <Notice tone="error" retry={() => void bootstrap.refresh()}>
                  {bootstrap.error}
                </Notice>
              ) : (
                <p className="loading" role="status">
                  Connecting to the quest…
                </p>
              )
            ) : (
              <>
                {bootstrap.error && (
                  <Notice tone="error" retry={() => void bootstrap.refresh()}>
                    Showing last saved event information. {bootstrap.error}
                  </Notice>
                )}
                {participantPreview ? (
                  data.viewer.can_manage_rooms ? (
                    <ParticipantPreview
                      slug={roomPreviewSlug}
                      staffData={data}
                    />
                  ) : (
                    <Notice tone="error">
                      Staff access is required to preview.{" "}
                      <Link href="/quest">Return to the quest</Link>
                    </Notice>
                  )
                ) : path === "/quest" || path === "/quest/" ? (
                  staff ? (
                    <StaffHome data={data} />
                  ) : (
                    <Lobby data={data} />
                  )
                ) : path.startsWith("/quest/rooms/") ? (
                  data.viewer.role === "maintainer" ? (
                    <StaffHome data={data} />
                  ) : (
                    <RoomPage
                      slug={decode(
                        path.slice("/quest/rooms/".length).replace(/\/$/, ""),
                      )}
                      data={data}
                      onRefresh={bootstrap.refresh}
                    />
                  )
                ) : path === "/quest/standings" ? (
                  <StandingsPage viewer={data.viewer} />
                ) : path === "/quest/admin" ? (
                  data.viewer.can_manage_rooms ? (
                    <Admin
                      key={`${data.viewer.id}-${data.viewer.role}`}
                      viewer={data.viewer}
                    />
                  ) : (
                    <Notice tone="error">
                      Organizer access is required.{" "}
                      <Link href="/quest">Return to the rooms</Link>
                    </Notice>
                  )
                ) : (
                  <Notice>
                    This page isn’t part of the quest.{" "}
                    <Link href="/quest">Return to the rooms</Link>
                  </Notice>
                )}
              </>
            )}
          </main>
          <footer className="app-footer">
            <img src={`${branding}/redhat-on-light.svg`} alt="Red Hat" />
            <span className="footer-message">
              Open Tech Quest · Learn together. Solve together.
            </span>
            {data?.viewer.role === "participant" && data.viewer.team_id && (
              <a href="/team">Your team</a>
            )}
            <span className="app-version">
              <span className="sr-only">Plugin version </span>
              {pluginVersion === "dev"
                ? "Development build"
                : `v${pluginVersion}`}
            </span>
          </footer>
        </>
      )}
    </ParticipantSession>
  );
}
const root =
  document.getElementById("root") || document.getElementById("quest-root");
if (root)
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
