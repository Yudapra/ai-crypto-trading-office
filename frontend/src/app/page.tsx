"use client";
import dynamic from "next/dynamic";
import { Component, useState, type ReactNode } from "react";
import { useOffice } from "@/lib/useOffice";
import { ActivityFeed, Portfolio, RobotList } from "@/components/Panels";

const OfficeScene = dynamic(() => import("@/components/OfficeScene"), {
  ssr: false, loading: () => <div className="center muted">Memuat kantor 3D…</div>,
});

class SceneBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed
      ? <div className="center muted">Tampilan 3D (WebGL) tidak tersedia di perangkat ini. Panel status di bawah tetap menampilkan data nyata dari backend.</div>
      : this.props.children;
  }
}

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
type Tab = "robots" | "activity" | "portfolio";

export default function Home() {
  const data = useOffice(API);
  const [tab, setTab] = useState<Tab>("robots");
  const s = data.state;
  const dataBadge = !data.online ? "Terputus"
    : s?.mock_data_in_use ? "DATA MOCK (bukan pasar nyata)"
    : s?.data_provider === "live" ? "Data pasar nyata" : "Tanpa sumber data";

  return (
    <main className="app">
      <header className="banner">
        <b>SIMULASI · PAPER TRADING</b><span> — tidak ada uang sungguhan{s?.live_trading ? " (PERINGATAN: backend melaporkan live)" : ""}</span>
        <span className={`badge ${data.online ? "ok" : "bad"}`}>{dataBadge}</span>
      </header>
      <section className="scene">
        <SceneBoundary><OfficeScene data={data} /></SceneBoundary>
        {!data.online && (
          <div className="overlay">Backend tidak terhubung ({API}). Robot ditampilkan abu-abu dan diam; tidak ada aktivitas yang dikarang.</div>
        )}
      </section>
      <nav className="tabs" role="tablist">
        {(["robots", "activity", "portfolio"] as Tab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
            {t === "robots" ? "Robot" : t === "activity" ? "Aktivitas" : "Portofolio"}
          </button>
        ))}
      </nav>
      <section className="panel">
        {tab === "robots" && <RobotList data={data} />}
        {tab === "activity" && <ActivityFeed data={data} />}
        {tab === "portfolio" && <Portfolio data={data} />}
      </section>
    </main>
  );
}
