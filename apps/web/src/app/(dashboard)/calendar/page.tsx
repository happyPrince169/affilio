import { redirect } from "next/navigation";

/** The publishing calendar lives inside the Distribution Center since M3A. */
export default function PublishingCalendarRedirect() {
  redirect("/distribution/calendar");
}
