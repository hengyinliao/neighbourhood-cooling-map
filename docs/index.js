document.addEventListener("DOMContentLoaded", function () {
    const buttons = document.querySelectorAll(".mode-buttons button");
    // const scenarioButtons = document.querySelectorAll(".scenario-buttons button");
    const scenarioButtons = document.querySelectorAll(".chips button");
    const help = document.getElementById("mode-help");
    const scenarioHelp = document.getElementById("scenario-help");
    const helpText = {
        standard: "Standard mode shows all controls and regular text size.",
        senior: "Large text mode increases labels, popups, and map markers.",
        colorblind: "High contrast mode uses stronger borders, words, and a colorblind-friendly palette.",
        simple: "Simple view hides technical notes and extra layer controls for a calmer public display."
    };
    const scenarioText = {
        all: "All mode shows all mapped resources.",
        senior: "Senior mode highlights places to sit, washrooms, water, and civic cooling places.",
        family: "Family mode highlights water, washrooms, civic buildings, parks, and child-friendly rest options.",
        heat: "Extreme heat mode highlights cooling places, water, washrooms, and transit.",
        resident: "Resident input mode highlights questionnaire and workshop feedback."
    };
    const scenarioByButtonClass = {
        general: "all",
        family: "family",
        senior: "senior",
        "extreme-heat": "heat"
    };

    function getScenarioFromButton(button) {
        return Object.entries(scenarioByButtonClass).find(([className]) => button.classList.contains(className))?.[1] || "all";
    }

    function setMode(mode) {
        document.body.classList.remove("access-senior", "access-colorblind", "access-simple");
        if (mode === "senior") document.body.classList.add("access-senior");
        if (mode === "colorblind") document.body.classList.add("access-colorblind");
        if (mode === "simple") document.body.classList.add("access-simple");
        buttons.forEach((button) => {
            button.classList.toggle("active", button.dataset.mode === mode);
            button.setAttribute("aria-pressed", button.dataset.mode === mode ? "true" : "false");
        });
        if (help) help.textContent = helpText[mode] || helpText.standard;
        localStorage.setItem("coolingMapMode", mode);
    }

    function setScenario(scenario) {
        const activeScenario = scenarioText[scenario] ? scenario : "all";
        document.body.classList.remove("scenario-senior", "scenario-family", "scenario-heat", "scenario-resident");
        if (activeScenario !== "all") document.body.classList.add("scenario-" + activeScenario);
        scenarioButtons.forEach((button) => {
            const buttonScenario = getScenarioFromButton(button);
            // button.classList.toggle("active", button.dataset.scenario === scenario);
            button.classList.toggle("selected", buttonScenario === activeScenario);
            // button.setAttribute("aria-pressed", button.dataset.scenario === scenario ? "true" : "false");
            button.setAttribute("aria-pressed", buttonScenario === activeScenario ? "true" : "false");
        });
        if (scenarioHelp) scenarioHelp.textContent = scenarioText[activeScenario] || scenarioText.all;
        localStorage.setItem("coolingMapScenario", activeScenario);
    }

    function setupLegendLayerBridge(attempt = 0) {
        const legendButtons = Array.from(document.querySelectorAll(".legend-item[data-layer]"));
        if (!legendButtons.length) return;

        const normalize = (value) => (value || "").replace(/\s+/g, " ").trim();
        const layerLabels = Array.from(document.querySelectorAll(".leaflet-control-layers-overlays label"));

        if (!layerLabels.length) {
            if (attempt < 60) {
                window.setTimeout(() => setupLegendLayerBridge(attempt + 1), 50);
            }
            return;
        }

        const inputByLayer = new Map();
        layerLabels.forEach((label) => {
            const input = label.querySelector("input.leaflet-control-layers-selector");
            if (!input) return;

            const labelClone = label.cloneNode(true);
            labelClone.querySelectorAll("input").forEach((inputNode) => inputNode.remove());
            inputByLayer.set(normalize(labelClone.textContent), input);
        });

        const setLegendState = (layerName) => {
            const input = inputByLayer.get(layerName);
            if (!input) return;

            legendButtons
                .filter((button) => normalize(button.dataset.layer) === layerName)
                .forEach((button) => {
                    button.classList.toggle("is-off", !input.checked);
                    button.setAttribute("aria-pressed", input.checked ? "true" : "false");
                });
        };

        legendButtons.forEach((button) => {
            const layerName = normalize(button.dataset.layer);
            const input = inputByLayer.get(layerName);

            if (!input) {
                button.disabled = true;
                button.setAttribute("aria-disabled", "true");
                return;
            }

            button.setAttribute("aria-label", `Toggle ${normalize(button.textContent)}`);
            setLegendState(layerName);
            button.addEventListener("click", () => {
                input.click();
                setLegendState(layerName);
            });
        });

        inputByLayer.forEach((input, layerName) => {
            input.addEventListener("change", () => setLegendState(layerName));
        });
    }

    function findLeafletSearchControl() {
        for (const key in window) {
            try {
                const value = window[key];
                if (
                    value
                    && typeof value.searchText === "function"
                    && typeof value._handleKeypress === "function"
                    && value._input?.classList?.contains("search-input")
                ) {
                    return value;
                }
            } catch {
                // Some browser globals are not readable in all contexts.
            }
        }
        return null;
    }

    function setupPanelSearchBridge(attempt = 0) {
        const panelInput = document.querySelector("[data-leaflet-search]");
        if (!panelInput) return;

        const searchControl = findLeafletSearchControl();
        if (!searchControl) {
            if (attempt < 60) {
                window.setTimeout(() => setupPanelSearchBridge(attempt + 1), 50);
            }
            return;
        }

        const pluginInput = searchControl._input;
        const tooltip = searchControl._tooltip || document.querySelector(".leaflet-control-search .search-tooltip");
        const bridge = panelInput.closest(".search-bridge");

        if (tooltip && bridge && tooltip.parentElement !== bridge) {
            tooltip.classList.add("panel-search-tooltip");
            bridge.appendChild(tooltip);
        }

        if (pluginInput) {
            pluginInput.setAttribute("aria-hidden", "true");
            pluginInput.tabIndex = -1;
        }

        panelInput.addEventListener("input", () => {
            searchControl.searchText(panelInput.value);
        });

        panelInput.addEventListener("keydown", (event) => {
            if (event.key !== "Enter") return;

            event.preventDefault();
            searchControl.searchText(panelInput.value);
            window.clearTimeout(searchControl.timerKeypress);
            if (panelInput.value && typeof searchControl._fillRecordsCache === "function") {
                searchControl._fillRecordsCache();
            }
            searchControl._handleKeypress({ keyCode: 13 });
        });

        if (typeof searchControl.on === "function") {
            searchControl.on("search:locationfound", (event) => {
                panelInput.value = event.text || searchControl._input?.value || panelInput.value;
            });
            searchControl.on("search:cancel", () => {
                panelInput.value = "";
            });
        }
    }

    buttons.forEach((button) => {
        button.addEventListener("click", function () {
            setMode(button.dataset.mode);
        });
    });
    scenarioButtons.forEach((button) => {
        button.addEventListener("click", function () {
            // setScenario(button.dataset.scenario);
            setScenario(getScenarioFromButton(button));
        });
    });
    setMode(localStorage.getItem("coolingMapMode") || "standard");
    setScenario(localStorage.getItem("coolingMapScenario") || "all");
    setupLegendLayerBridge();
    setupPanelSearchBridge();

    // load weather data for Riley Park - Little Mountain
    const widget = document.querySelector(".weather-widget");
          const fields = {
            temp: document.querySelector("[data-weather-temp]"),
            low: document.querySelector("[data-weather-low]"),
            high: document.querySelector("[data-weather-high]"),
            cloud: document.querySelector("[data-weather-cloud]"),
            cloudCover: document.querySelector("[data-weather-cloud-cover]"),
            uv: document.querySelector("[data-weather-uv]"),
            status: document.querySelector("[data-weather-status]")
          };
          const rileyParkLocation = { latitude: 49.2447, longitude: -123.1039, label: "Riley Park - Little Mountain" };

          const round = (value) => Math.round(Number(value));
          const formatUv = (value) => Number(value).toFixed(1).replace(".0", "");

          const getCloudLabel = (cover) => {
            if (cover < 20) return "Clear";
            if (cover < 50) return "Partly cloudy";
            if (cover < 80) return "Mostly cloudy";
            return "Cloudy";
          };

          const getHeatLevel = (temp) => {
            if (temp >= 35) return "extreme";
            if (temp >= 29) return "hot";
            if (temp >= 24) return "warm";
            return "mild";
          };

          const getBackground = (hour, temp) => {
            const heat = getHeatLevel(temp);
            const isNight = hour >= 21 || hour < 5;
            const isMorning = hour >= 5 && hour < 10;
            const isEvening = hour >= 18 && hour < 21;
            const heatGlow = {
              mild: "rgba(195, 229, 251, 0.50)",
              warm: "rgba(253, 255, 198, 0.60)",
              hot: "rgba(255, 213, 145, 0.78)",
              extreme: "rgba(255, 248, 225, 1)"
            }[heat];

            if (isNight) {
              return `radial-gradient(circle at 78% 18%, ${heatGlow}, transparent 34%), linear-gradient(145deg, #17204d, #233e73 54%, #0d172e)`;
            }

            if (isMorning) {
              return `radial-gradient(circle at 76% 18%, ${heatGlow}, transparent 36%), linear-gradient(145deg, #19a4ff, #55bfe0 52%, #b3ffee)`;
            }

            if (isEvening) {
              return `radial-gradient(circle at 80% 22%, ${heatGlow}, transparent 36%), linear-gradient(145deg, #734d9f, #e07155 56%, #f7b660)`;
            }

            return `radial-gradient(circle at 28% 18%, ${heatGlow}, transparent 36%), linear-gradient(145deg, #117bc1, #55bfe0 75%, #b7ebff)`;
          };

          const fetchWeather = async ({ latitude, longitude, label }) => {
            const params = new URLSearchParams({
              latitude,
              longitude,
              current: "temperature_2m,cloud_cover,uv_index",
              daily: "temperature_2m_max,temperature_2m_min",
              forecast_days: "1",
              temperature_unit: "celsius",
              timezone: "auto"
            });
            const response = await fetch(`https://api.open-meteo.com/v1/forecast?${params}`);

            if (!response.ok) {
              throw new Error("Weather request failed");
            }

            const data = await response.json();
            const current = data.current;
            const daily = data.daily;
            const temp = round(current.temperature_2m);
            const cloudCover = round(current.cloud_cover);
            const hour = Number(current.time.slice(11, 13));

            fields.temp.textContent = temp;
            fields.low.textContent = `${round(daily.temperature_2m_min[0])}°`;
            fields.high.textContent = `${round(daily.temperature_2m_max[0])}°`;
            fields.cloud.textContent = getCloudLabel(cloudCover);
            fields.cloudCover.textContent = `${cloudCover}%`;
            fields.uv.textContent = formatUv(current.uv_index);
            fields.status.textContent = `Weather updated for ${label || data.timezone}`;
            widget.style.background = getBackground(hour, temp);
          };

          const showWeatherError = () => {
            fields.cloud.textContent = "Unavailable";
            fields.status.textContent = "Weather data is unavailable right now.";
          };
          fetchWeather(rileyParkLocation).catch(showWeatherError);
});
