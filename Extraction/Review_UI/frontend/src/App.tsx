import { useEffect, useState } from "react";
import BatchReview from "./BatchReview";
import Home from "./Home";

type Route = { page: "home" } | { page: "review"; runId: string };

function parseRoute(hash: string): Route {
  const match = hash.match(/^#\/review\/(.+)$/);
  return match ? { page: "review", runId: decodeURIComponent(match[1]) } : { page: "home" };
}

export default function App() {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));

  useEffect(() => {
    const onHash = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  if (route.page === "review") {
    return <BatchReview runId={route.runId} onHome={() => (window.location.hash = "#/")} />;
  }
  return <Home onReview={(runId) => (window.location.hash = `#/review/${encodeURIComponent(runId)}`)} />;
}
