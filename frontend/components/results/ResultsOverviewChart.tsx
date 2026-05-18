"use client";

import ReactECharts from "echarts-for-react";

import { Asset } from "@/lib/types";

export function ResultsOverviewChart({
  accepted,
  candidate,
  other,
}: {
  accepted: Asset[];
  candidate: Asset[];
  other: Asset[];
}) {
  const option = {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    grid: { left: 24, right: 18, top: 28, bottom: 24, containLabel: true },
    xAxis: {
      type: "category",
      data: ["Accepted", "Candidate", "Other"],
      axisLine: { lineStyle: { color: "rgba(157, 175, 201, 0.24)" } },
      axisLabel: { color: "#9cb0cd" },
    },
    yAxis: {
      type: "value",
      axisLine: { show: false },
      splitLine: { lineStyle: { color: "rgba(157, 175, 201, 0.12)" } },
      axisLabel: { color: "#9cb0cd" },
    },
    series: [
      {
        type: "bar",
        data: [
          { value: accepted.length, itemStyle: { color: "#3fb37b" } },
          { value: candidate.length, itemStyle: { color: "#f0af44" } },
          { value: other.length, itemStyle: { color: "#71819c" } },
        ],
        barWidth: 34,
        label: { show: true, position: "top", color: "#edf2fb" },
      },
    ],
  };

  return (
    <section className="panel">
      <div className="panel-header">
        <h3>Results Overview</h3>
        <span>Status distribution</span>
      </div>
      <div className="panel-body">
        <ReactECharts option={option} style={{ height: 260 }} notMerge lazyUpdate />
      </div>
    </section>
  );
}
