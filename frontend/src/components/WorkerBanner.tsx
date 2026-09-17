import { useEffect } from "react";
import { api } from "../api";
import { useApi } from "../useApi";

/**
 * Nothing in the app runs without a worker draining the queue, and its absence is
 * invisible: jobs just say "Queued" forever, calibration never finishes, and no
 * screen says why. This is the one place that names the cause.
 *
 * It is also the app's connectivity indicator — if this call fails, the API is down
 * and every other panel on screen is stale.
 */
export function WorkerBanner() {
  const { data, error, loading, reload } = useApi<any>(() => api.health(), []);

  // Re-check periodically so the banner clears itself once the API is back, rather
  // than accusing a working server of being down until the user reloads. A hidden
  // tab neither needs the answer nor should it keep the radio busy.
  useEffect(() => {
    const t = setInterval(() => {
      if (document.visibilityState === "visible") reload();
    }, 15000);
    return () => clearInterval(t);
  }, [reload]);

  // Nothing to say before the first answer arrives.
  if (loading && !data && !error) return null;

  if (error)
    return (
      <div className="err-box" role="alert" style={{ marginBottom: 14 }}>
        <b>Not connected to the Snitch API.</b>{" "}
        <span className="tiny">
          Everything below is stale or empty because the request failed — it is not a
          report about your monitors. ({error})
        </span>
      </div>
    );

  if (!data?.worker || data.worker.ok) return null;

  return (
    <div className="warn-box" role="alert" style={{ marginBottom: 14 }}>
      <b>
        {data.worker.stalled_jobs} job{data.worker.stalled_jobs === 1 ? "" : "s"} queued
        with nothing running them.
      </b>{" "}
      <span className="tiny">
        Calibration, scheduled polls, and exports will not finish until a worker is
        started. {data.worker.hint}
      </span>
    </div>
  );
}
