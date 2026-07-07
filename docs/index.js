document.addEventListener("DOMContentLoaded", function () {
    const data = window.COOLING_MAP_DATA;
    const mapElement = document.getElementById("map");

    if (!data || !mapElement) {
        console.error("Cooling map data is unavailable. Run scripts/build_page.py first.");
        return;
    }

    const pointStyles = {
        cooling: {
            icon: "assets/icons/tempLow.svg",
            className: "cooling-marker",
            popupType: "Cooling place",
            help: "A community centre or library that may be useful during hot weather. Check current opening hours before going."
        },
        water: {
            icon: "assets/icons/waterRefill.svg",
            className: "water-marker",
            popupType: "Water",
            help: "A public drinking fountain or bottle filling location."
        },
        washroom: {
            icon: "assets/icons/restroom.svg",
            className: "washroom-marker",
            popupType: "Washroom",
            help: "A public washroom. Hours and access may change by season."
        },
        bench: {
            icon: "assets/icons/chair.svg",
            className: "bench-marker",
            popupType: "Place to sit",
            help: "A mapped bench or place to sit from OpenStreetMap."
        },
        bus: {
            icon: "assets/icons/bus.svg",
            className: "transit-marker",
            popupType: "Transit stop",
            help: "A mapped bus or transit stop from OpenStreetMap."
        },
        train: {
            icon: "assets/icons/subway.svg",
            className: "rapid-marker",
            popupType: "Train or rapid transit station",
            help: "A SkyTrain or rapid transit station."
        },
        residentGood: {
            icon: "assets/icons/thumbsUp.svg",
            className: "resident-good-marker",
            popupType: "Resident-recommended place",
            help: "A place residents identified as useful, comfortable, or worth knowing about."
        },
        residentHot: {
            icon: "assets/icons/tempHigh.svg",
            className: "resident-hot-marker",
            popupType: "Resident-identified hot spot",
            help: "A place residents identified as hot, uncomfortable, or needing attention."
        },
        residentNeed: {
            icon: "assets/icons/suggestion.svg",
            className: "resident-need-marker",
            popupType: "Resident suggestion",
            help: "A place residents suggested for improvement, clearer information, or future action."
        }
    };

    const layerRegistry = new Map();
    const parkSearchRecords = [];
    const bufferMetres = data.config?.buffer_metres || 500;
    const contextLayerNames = [
        `Nearby area, within about ${bufferMetres} m`,
        "Main project area"
    ];
    const scenarioLayerPresets = {
        all: "all",
        senior: [
            "Community centres and libraries",
            "Water fountains",
            "Public washrooms",
            "Benches and places to sit",
            "Parks and green spaces",
            "Possible shady walking routes"
        ],
        family: [
            "Community centres and libraries",
            "Parks and green spaces",
            "Water fountains",
            "Public washrooms",
            "Resident feedback and workshop notes",
            "Possible shady walking routes"
        ],
        heat: [
            "Possible shady walking routes",
            "Community centres and libraries",
            "Water fountains",
            "Public washrooms",
            "Bus and transit stops",
            "Train and rapid transit stations"
        ],
        resident: [
            "Resident feedback and workshop notes"
        ]
    };

    const normalize = (value) => (value || "").replace(/\s+/g, " ").trim();
    const escapeHtml = (value) => String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");

    const hasFeatures = (featureCollection) => Boolean(featureCollection?.features?.length);

    const map = L.map("map", {
        center: data.config?.center || [49.2528, -123.1049],
        zoom: data.config?.zoom || 15,
        zoomControl: true
    });

    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
        attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
        maxZoom: 20
    }).addTo(map);

    function registerLayer(name, layer, visible = true) {
        layerRegistry.set(normalize(name), { name, layer });
        if (visible) {
            layer.addTo(map);
        }
        return layer;
    }

    function getRegisteredLayer(name) {
        return layerRegistry.get(normalize(name));
    }

    function setLayerVisible(name, shouldShow) {
        const entry = getRegisteredLayer(name);
        if (!entry) return;

        const isVisible = map.hasLayer(entry.layer);
        if (shouldShow && !isVisible) {
            entry.layer.addTo(map);
        }
        if (!shouldShow && isVisible) {
            map.removeLayer(entry.layer);
        }
    }

    function syncLegendState() {
        document.querySelectorAll(".legend-item[data-layer]").forEach((button) => {
            const entry = getRegisteredLayer(button.dataset.layer);
            if (!entry) {
                button.disabled = true;
                button.setAttribute("aria-disabled", "true");
                return;
            }

            const isVisible = map.hasLayer(entry.layer);
            button.classList.toggle("is-off", !isVisible);
            button.setAttribute("aria-pressed", isVisible ? "true" : "false");
            button.setAttribute("aria-label", `Toggle ${normalize(button.textContent)}`);
        });
    }

    function popupHtml(properties, style) {
        const title = escapeHtml(properties.name || style.popupType);
        const category = escapeHtml(properties.category || style.popupType);
        const source = escapeHtml(properties.source || "Open data");
        const extraFields = [
            ["Resident note", "description"],
            ["Good for", "best_for"],
            ["Verified", "verified"],
            ["Session", "contact_or_session"]
        ];
        const extraHtml = extraFields
            .map(([label, field]) => {
                const value = properties[field];
                return value ? `<div><b>${label}:</b> ${escapeHtml(value)}</div>` : "";
            })
            .join("");

        return `
            <div class="resource-popup">
              <div class="popup-title">${title}</div>
              <div><b>What it is:</b> ${category}</div>
              <div><b>Helpful note:</b> ${escapeHtml(style.help)}</div>
              ${extraHtml}
              <div class="popup-source">Source: ${source}</div>
            </div>
        `;
    }

    function markerIcon(style) {
        return L.divIcon({
            html: `
                <div class="cooling-map-marker ${style.className}" aria-label="${escapeHtml(style.popupType)}">
                  <img src="${escapeHtml(style.icon)}" alt="" aria-hidden="true" />
                </div>
            `,
            iconSize: [46, 46],
            iconAnchor: [23, 23],
            popupAnchor: [0, -20],
            className: "cooling-div-icon"
        });
    }

    function residentStyle(properties) {
        const feedbackType = String(properties.feedback_type || "").toLowerCase();
        if (feedbackType.includes("hot")) return pointStyles.residentHot;
        if (feedbackType.includes("need") || feedbackType.includes("improvement")) return pointStyles.residentNeed;
        return pointStyles.residentGood;
    }

    function pointLayer(featureCollection, name, styleForFeature, cluster = false) {
        const target = cluster && L.markerClusterGroup ? L.markerClusterGroup() : L.featureGroup();
        if (!hasFeatures(featureCollection)) {
            return registerLayer(name, target, false);
        }

        L.geoJSON(featureCollection, {
            pointToLayer(feature, latlng) {
                const properties = feature.properties || {};
                const style = styleForFeature(properties);
                return L.marker(latlng, { icon: markerIcon(style) })
                    .bindTooltip(String(properties.name || name))
                    .bindPopup(popupHtml(properties, style), { maxWidth: 380 });
            }
        }).eachLayer((layer) => target.addLayer(layer));

        return registerLayer(name, target);
    }

    function addBoundaryLayers() {
        if (hasFeatures(data.project_area_buffer)) {
            registerLayer(
                contextLayerNames[0],
                L.geoJSON(data.project_area_buffer, {
                    style: { color: "#777777", weight: 1, fillOpacity: 0.03 }
                })
            );
        }

        if (hasFeatures(data.project_area)) {
            registerLayer(
                contextLayerNames[1],
                L.geoJSON(data.project_area, {
                    style: { color: "#333333", weight: 2, fillOpacity: 0.04 }
                })
            );
        }
    }

    function addParkLayer() {
        const parks = data.layers?.parks;
        if (!hasFeatures(parks)) return;

        const layer = L.geoJSON(parks, {
            style: { color: "#005a32", weight: 2, fillColor: "#88c999", fillOpacity: 0.52 },
            onEachFeature(feature, featureLayer) {
                const properties = feature.properties || {};
                const name = String(properties.name || "Park or green space");
                featureLayer.bindTooltip(`${escapeHtml(name)}<br>${escapeHtml(properties.category || "")}`);
                featureLayer.bindPopup(`
                    <div class="resource-popup">
                      <div class="popup-title">${escapeHtml(name)}</div>
                      <div><b>Type:</b> ${escapeHtml(properties.category || "Park or green space")}</div>
                    </div>
                `);
                parkSearchRecords.push({ name, layer: featureLayer });
            }
        });

        registerLayer("Parks and green spaces", layer);
    }

    function addShadedRouteLayer() {
        const routes = data.layers?.shaded_walking_routes;
        if (!hasFeatures(routes)) return;

        const layer = L.geoJSON(routes, {
            style(feature) {
                const category = feature.properties?.category;
                return {
                    color: category === "Higher shade potential" ? "#005a32" : "#5aae61",
                    weight: category === "Higher shade potential" ? 5 : 3,
                    opacity: 0.9,
                    dashArray: category === "Higher shade potential" ? null : "7, 5"
                };
            },
            onEachFeature(feature, featureLayer) {
                const properties = feature.properties || {};
                const trees = properties.trees_per_100m ? `${properties.trees_per_100m} public trees per 100 m` : "";
                featureLayer.bindTooltip(`
                    <strong>${escapeHtml(properties.name || "Walking route segment")}</strong><br>
                    ${escapeHtml(properties.category || "Shade estimate")}<br>
                    ${escapeHtml(trees)}
                `);
            }
        });

        registerLayer("Possible shady walking routes", layer);
    }

    function addPointLayers() {
        pointLayer(data.layers?.civic_cooling_places, "Community centres and libraries", () => pointStyles.cooling);
        pointLayer(data.layers?.drinking_fountains, "Water fountains", () => pointStyles.water);
        pointLayer(data.layers?.public_washrooms, "Public washrooms", () => pointStyles.washroom);
        pointLayer(data.layers?.benches_osm, "Benches and places to sit", () => pointStyles.bench, true);
        pointLayer(data.layers?.transit_stops_osm, "Bus and transit stops", () => pointStyles.bus, true);
        pointLayer(data.layers?.rapid_transit_stations, "Train and rapid transit stations", () => pointStyles.train);
        pointLayer(data.layers?.resident_input, "Resident feedback and workshop notes", residentStyle);
    }

    function setupLegendExpansionToggle() {
        document.querySelectorAll(".legend-toggle").forEach((toggle) => {
            const legend = toggle.closest("section")?.querySelector(".legend");
            if (!legend) return;

            const setExpanded = (isExpanded) => {
                legend.setAttribute("aria-expanded", isExpanded ? "true" : "false");
                legend.classList.toggle("extended", isExpanded);
                toggle.setAttribute("aria-expanded", isExpanded ? "true" : "false");
                toggle.setAttribute("aria-label", `${isExpanded ? "Collapse" : "Expand"} cooling nearby legend`);
            };

            toggle.addEventListener("click", () => {
                setExpanded(legend.getAttribute("aria-expanded") !== "true");
            });
            toggle.addEventListener("keydown", (event) => {
                if (event.key !== "Enter" && event.key !== " ") return;

                event.preventDefault();
                setExpanded(legend.getAttribute("aria-expanded") !== "true");
            });
        });
    }

    function setupLegendLayerBridge() {
        document.querySelectorAll(".legend-item[data-layer]").forEach((button) => {
            button.addEventListener("click", () => {
                const entry = getRegisteredLayer(button.dataset.layer);
                if (!entry) return;
                setLayerVisible(entry.name, !map.hasLayer(entry.layer));
                syncLegendState();
            });
        });
        syncLegendState();
    }

    function applyScenarioLayerPreset(scenario) {
        const preset = scenarioLayerPresets[scenario] || scenarioLayerPresets.all;
        const desiredLayers = preset === "all"
            ? null
            : new Set([...contextLayerNames, ...preset].map(normalize));

        layerRegistry.forEach((entry, layerName) => {
            setLayerVisible(entry.name, desiredLayers === null || desiredLayers.has(layerName));
        });
        syncLegendState();
    }

    function setScenario(scenario) {
        const activeScenario = scenarioLayerPresets[scenario] ? scenario : "all";
        document.body.classList.remove("scenario-senior", "scenario-family", "scenario-heat", "scenario-resident");
        if (activeScenario !== "all") {
            document.body.classList.add(`scenario-${activeScenario}`);
        }

        document.querySelectorAll(".chips button[data-scenario]").forEach((button) => {
            const isSelected = button.dataset.scenario === activeScenario;
            button.classList.toggle("selected", isSelected);
            button.setAttribute("aria-pressed", isSelected ? "true" : "false");
        });

        localStorage.setItem("coolingMapScenario", activeScenario);
        applyScenarioLayerPreset(activeScenario);
    }

    function setupScenarioButtons() {
        document.querySelectorAll(".chips button[data-scenario]").forEach((button) => {
            button.addEventListener("click", () => setScenario(button.dataset.scenario));
        });
        setScenario(localStorage.getItem("coolingMapScenario") || "all");
    }

    function setupParkSearch() {
        const input = document.querySelector("[data-park-search]");
        const bridge = input?.closest(".search-bridge");
        if (!input || !bridge) return;

        const results = document.createElement("ul");
        results.className = "panel-search-tooltip";
        bridge.appendChild(results);

        const closeResults = () => {
            results.classList.remove("is-open");
            results.innerHTML = "";
        };

        const selectRecord = (record) => {
            setLayerVisible("Parks and green spaces", true);
            syncLegendState();
            map.fitBounds(record.layer.getBounds(), { maxZoom: 17, padding: [40, 40] });
            record.layer.openPopup();
            input.value = record.name;
            closeResults();
        };

        const renderResults = () => {
            const query = normalize(input.value).toLowerCase();
            results.innerHTML = "";
            if (!query) {
                closeResults();
                return;
            }

            const matches = parkSearchRecords
                .filter((record) => record.name.toLowerCase().includes(query))
                .slice(0, 8);

            matches.forEach((record) => {
                const item = document.createElement("li");
                const button = document.createElement("button");
                button.type = "button";
                button.textContent = record.name;
                button.addEventListener("click", () => selectRecord(record));
                item.appendChild(button);
                results.appendChild(item);
            });

            results.classList.toggle("is-open", matches.length > 0);
        };

        input.addEventListener("input", renderResults);
        input.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                closeResults();
            }
            if (event.key === "Enter") {
                const query = normalize(input.value).toLowerCase();
                const firstMatch = query
                    ? parkSearchRecords.find((record) => record.name.toLowerCase().includes(query))
                    : null;
                if (firstMatch) {
                    event.preventDefault();
                    selectRecord(firstMatch);
                }
            }
        });
        document.addEventListener("click", (event) => {
            if (!bridge.contains(event.target)) closeResults();
        });
    }

    function setupLocationButton() {
        const button = document.querySelector("[data-location-button]");
        if (!button || !navigator.geolocation) return;

        let locationMarker = null;
        button.addEventListener("click", () => {
            button.disabled = true;
            button.textContent = "Finding location...";
            navigator.geolocation.getCurrentPosition(
                (position) => {
                    const latlng = [position.coords.latitude, position.coords.longitude];
                    if (locationMarker) {
                        locationMarker.setLatLng(latlng);
                    } else {
                        locationMarker = L.circleMarker(latlng, {
                            radius: 8,
                            color: "#1063c7",
                            weight: 3,
                            fillColor: "#ffffff",
                            fillOpacity: 1
                        }).addTo(map).bindPopup("Your approximate location");
                    }
                    map.setView(latlng, 17);
                    locationMarker.openPopup();
                    button.disabled = false;
                    button.textContent = "Use My Location";
                },
                () => {
                    button.disabled = false;
                    button.textContent = "Location unavailable";
                    window.setTimeout(() => {
                        button.textContent = "Use My Location";
                    }, 2500);
                },
                { enableHighAccuracy: true, timeout: 10000 }
            );
        });
    }

    function setupWeatherWidget() {
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
        if (!widget || Object.values(fields).some((field) => !field)) return;

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
                return `radial-gradient(circle at 83% 42%, ${heatGlow}, transparent 36%), linear-gradient(156deg, #7ca2ff, #ff9479 56%, #f7b660)`;
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

            const weatherData = await response.json();
            const current = weatherData.current;
            const daily = weatherData.daily;
            const temp = round(current.temperature_2m);
            const cloudCover = round(current.cloud_cover);
            const hour = Number(current.time.slice(11, 13));

            fields.temp.textContent = temp;
            fields.low.textContent = `${round(daily.temperature_2m_min[0])}°`;
            fields.high.textContent = `${round(daily.temperature_2m_max[0])}°`;
            fields.cloud.textContent = getCloudLabel(cloudCover);
            fields.cloudCover.textContent = `${cloudCover}%`;
            fields.uv.textContent = formatUv(current.uv_index);
            fields.status.textContent = `Weather updated for ${label || weatherData.timezone}`;
            widget.style.background = getBackground(hour, temp);
        };

        const showWeatherError = () => {
            fields.cloud.textContent = "Unavailable";
            fields.status.textContent = "Weather data is unavailable right now.";
        };

        fetchWeather(rileyParkLocation).catch(showWeatherError);
    }

    addBoundaryLayers();
    addParkLayer();
    addShadedRouteLayer();
    addPointLayers();
    setupLegendExpansionToggle();
    setupLegendLayerBridge();
    setupScenarioButtons();
    setupParkSearch();
    setupLocationButton();
    setupWeatherWidget();
});
