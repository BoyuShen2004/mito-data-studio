import type { PeopleOverview, Person, ProfileUpdate } from "../types/people";
import type { CurrentUser } from "../types";
import { api } from "./client";

/** The whole People page in one round trip, role-scoped server-side. */
export const getPeopleOverview = () =>
  api.get<PeopleOverview>("/people/overview/");

/** Read-only card for one person (`/people/:username`). */
export const getPerson = (username: string) =>
  api.get<Person>(`/people/${encodeURIComponent(username)}/`);

/** Edit your own short profile; returns the refreshed current user. */
export const updateMyProfile = (data: ProfileUpdate) =>
  api.patch<CurrentUser>("/people/me/", data);
