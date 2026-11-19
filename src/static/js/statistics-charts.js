document.addEventListener("DOMContentLoaded", function () {
  const textColor = "#D1D5DB";
  const gridColor = "rgba(255, 255, 255, 0.06)";
  const numberFormat = new Intl.NumberFormat(document.documentElement.lang);
  const percentFormat = new Intl.NumberFormat(document.documentElement.lang, {
    style: "percent",
    maximumFractionDigits: 1,
  });

  function formatDuration(totalMinutes) {
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    if (!hours) return `${minutes} min`;
    return minutes ? `${numberFormat.format(hours)} h ${minutes} min` : `${numberFormat.format(hours)} h`;
  }

  function readChartData(id) {
    return JSON.parse(document.getElementById(id).textContent);
  }

  function renderDistribution(listId, dataId, formatValue, showShare = false) {
    const list = document.getElementById(listId);
    if (!list) return;

    const { labels, datasets: [series] } = readChartData(dataId);
    const total = series.data.reduce((sum, value) => sum + value, 0);
    labels.forEach((label, index) => {
      const value = series.data[index];
      const share = total ? value / total : 0;
      const row = document.createElement("li");
      row.className = "statistics-bar-row";

      const name = document.createElement("span");
      name.textContent = showShare ? `${label} (${percentFormat.format(share)})` : label;

      const amount = document.createElement("span");
      amount.className = "statistics-bar-value";
      amount.textContent = formatValue(value);

      const track = document.createElement("span");
      track.className = "statistics-bar-track";
      track.setAttribute("aria-hidden", "true");

      const fill = document.createElement("span");
      fill.className = "statistics-bar-fill";
      fill.style.width = `${share * 100}%`;
      fill.style.backgroundColor = series.backgroundColor[index];

      track.append(fill);
      row.append(name, amount, track);
      list.append(row);
    });
  }

  renderDistribution("media-type-bars", "media_type_distribution", (count) =>
    `${numberFormat.format(count)} ${count === 1 ? "item" : "items"}`
  );
  renderDistribution("watch-time-bars", "watch_time_distribution", formatDuration, true);

  const statusChart = document.getElementById("statusStackedChart");
  if (statusChart) {
    const data = readChartData("status_distribution");
    data.datasets = data.datasets.filter((series) => series.data.some((value) => value > 0));

    new Chart(statusChart, {
      type: "bar",
      data,
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        datasets: {
          bar: { borderRadius: 4, maxBarThickness: 20 },
        },
        scales: {
          x: {
            stacked: true,
            beginAtZero: true,
            border: { display: false },
            grid: { color: gridColor, drawTicks: false },
            ticks: { color: textColor, precision: 0, padding: 8, maxTicksLimit: 6 },
          },
          y: {
            stacked: true,
            border: { display: false },
            grid: { display: false },
            ticks: { color: textColor, padding: 8, autoSkip: false },
          },
        },
        interaction: { mode: "index", axis: "y", intersect: false },
        plugins: {
          legend: {
            position: "bottom",
            labels: {
              color: textColor,
              usePointStyle: true,
              pointStyle: "rectRounded",
              padding: 12,
              boxWidth: 10,
            },
          },
          tooltip: {
            filter: (item) => item.parsed.x > 0,
            callbacks: {
              footer(items) {
                if (items.length > 1) {
                  const total = items.reduce((sum, item) => sum + item.parsed.x, 0);
                  return `Total: ${numberFormat.format(total)}`;
                }
                return "";
              },
            },
          },
        },
      },
    });
  }

  const watchTimeChart = document.getElementById("watchTimeChart");
  if (watchTimeChart) {
    const data = readChartData("watch_time_timeseries");
    const fewPeriods = data.labels.length <= 7;

    new Chart(watchTimeChart, {
      type: "bar",
      data,
      options: {
        responsive: true,
        maintainAspectRatio: false,
        datasets: {
          bar: {
            backgroundColor: "#6366f1",
            borderRadius: 5,
            maxBarThickness: 28,
            barPercentage: fewPeriods ? 0.3 : 0.65,
            categoryPercentage: fewPeriods ? 0.5 : 0.8,
          },
        },
        interaction: { mode: "index", axis: "x", intersect: false },
        scales: {
          x: {
            border: { display: false },
            grid: { display: false },
            ticks: {
              color: textColor,
              maxRotation: 0,
              maxTicksLimit: 6,
              callback(index) {
                return this.getLabelForValue(index).replace(/^Semana /, "");
              },
            },
          },
          y: {
            beginAtZero: true,
            border: { display: false },
            grid: { color: gridColor, drawTicks: false },
            ticks: { color: textColor, padding: 8, precision: 0, maxTicksLimit: 6, callback: formatDuration },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: { label: (item) => formatDuration(item.parsed.y) },
          },
        },
      },
    });
  }
});
