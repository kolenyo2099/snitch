import { Component, ErrorInfo, ReactNode } from "react";

/** The one place a failed load is rendered. Never fall through to an empty page:
 *  "no credentials set" and "could not ask about credentials" look identical to a
 *  user, and only one of them is a fact about their system. */
export function ErrorBox({ error, onRetry, what }:
  { error: string; onRetry?: () => void; what?: string }) {
  return (
    <div className="err-box" role="alert" style={{ marginBottom: 12 }}>
      <b>{what ? `${what} could not be loaded.` : "Could not load this."}</b>
      <div className="tiny" style={{ marginTop: 4 }}>{error}</div>
      <p className="tiny" style={{ margin: "6px 0 0" }}>
        This is a problem reaching TerraWatch's API — not a statement about your data.
        Check that the API process is running.
      </p>
      {onRetry && <button style={{ marginTop: 8 }} onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <p className="muted" role="status" aria-live="polite">{what}…</p>;
}

/** Guards a page or panel: an error always wins over an empty render. */
export function Async<T>({ state, what, children }: {
  state: { data?: T; error?: string; loading: boolean; reload: () => void };
  what?: string;
  children: (data: T) => ReactNode;
}) {
  if (state.error && state.data === undefined)
    return <ErrorBox error={state.error} what={what} onRetry={state.reload} />;
  if (state.data === undefined)
    return <Loading what={what ? `Loading ${what}` : undefined} />;
  return (
    <>
      {state.error && <ErrorBox error={state.error} what={what} onRetry={state.reload} />}
      {children(state.data)}
    </>
  );
}

/** A render crash used to blank the whole app with only a console trace. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { err?: Error }> {
  state: { err?: Error } = {};
  static getDerivedStateFromError(err: Error) { return { err }; }
  componentDidCatch(err: Error, info: ErrorInfo) { console.error(err, info); }
  render() {
    if (!this.state.err) return this.props.children;
    return (
      <div className="err-box" role="alert">
        <b>This screen hit an error and stopped rendering.</b>
        <pre className="tiny" style={{ whiteSpace: "pre-wrap" }}>{this.state.err.message}</pre>
        <button onClick={() => this.setState({ err: undefined })}>Try again</button>
      </div>
    );
  }
}
