import { useLocation, useNavigate } from "react-router-dom";

/** True when an earlier in-app page exists to return to.
 *
 * React Router tracks its position in the history stack on
 * `window.history.state.idx`. An idx of 0 means this is the first entry — a
 * fresh load, a deep link, or the post-login `replace` — so `navigate(-1)`
 * would leave the app entirely. In that case callers fall back to the parent
 * route instead. */
function useCanGoBack(): boolean {
  useLocation(); // re-reads history.state after every navigation
  const idx = (window.history.state as { idx?: number } | null)?.idx;
  return typeof idx === "number" && idx > 0;
}

/** "Back" for a page that has a parent. Returns to the previous page when there
 *  is one, otherwise goes up to `fallback`. */
export default function BackButton({ fallback }: { fallback: string }) {
  const navigate = useNavigate();
  const canGoBack = useCanGoBack();

  return (
    <button
      type="button"
      className="back-btn"
      onClick={() => (canGoBack ? navigate(-1) : navigate(fallback))}
    >
      ← Back
    </button>
  );
}
