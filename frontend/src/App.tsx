import { NavLink, Route, Routes } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import Projects from "./pages/Projects";
import ProjectDetail from "./pages/ProjectDetail";
import RunDetail from "./pages/RunDetail";
import AlertDetail from "./pages/AlertDetail";
import Wizard from "./pages/Wizard";
import Settings from "./pages/Settings";

export default function App() {
  return (
    <div className="app">
      <nav className="side">
        <h1>TERRA<span>WATCH</span></h1>
        <NavLink to="/" end>Dashboard</NavLink>
        <NavLink to="/projects">Projects</NavLink>
        <NavLink to="/new">New monitor</NavLink>
        <NavLink to="/settings">Settings</NavLink>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/projects/:uuid" element={<ProjectDetail />} />
          <Route path="/runs/:uuid" element={<RunDetail />} />
          <Route path="/alerts/:uuid" element={<AlertDetail />} />
          <Route path="/new" element={<Wizard />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </div>
  );
}
