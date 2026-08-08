import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  fetchMe,
  login as apiLogin,
  logout as apiLogout,
  register as apiRegister,
  type LoginPortal,
  type RegisterInput,
} from "../api/auth";
import { getToken, setToken } from "../api/client";
import type { CurrentUser } from "../types";

interface AuthState {
  user: CurrentUser | null;
  loading: boolean;
  login: (
    username: string,
    password: string,
    portal?: LoginPortal,
  ) => Promise<CurrentUser>;
  register: (data: RegisterInput) => Promise<CurrentUser>;
  logout: () => Promise<void>;
  /** Re-fetch the current user — call after editing your own profile so
   * the navbar/People card reflect the new display name immediately. */
  refresh: () => Promise<void>;
  isManager: boolean;
  isRequester: boolean;
  isAnnotator: boolean;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!getToken()) {
      setLoading(false);
      return;
    }
    fetchMe()
      .then(setUser)
      .catch(() => {
        // Token is stale/invalid (e.g. backend DB was reset) — drop it so
        // we don't keep retrying it and logging a 401 on every reload.
        setToken(null);
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      isManager: user?.role === "manager",
      isRequester: user?.role === "requester" || user?.role === "client",
      isAnnotator: user?.role === "annotator",
      login: async (username, password, portal) => {
        const u = await apiLogin(username, password, portal);
        setUser(u);
        return u;
      },
      register: async (data) => {
        const u = await apiRegister(data);
        setUser(u);
        return u;
      },
      logout: async () => {
        await apiLogout();
        setUser(null);
      },
      refresh: async () => {
        if (!getToken()) return;
        try {
          setUser(await fetchMe());
        } catch {
          // A failed refresh is not worth logging anyone out over — the
          // caller only wanted fresher profile fields.
        }
      },
    }),
    [user, loading],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
