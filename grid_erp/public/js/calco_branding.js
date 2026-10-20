(function () {
  if (window.__calcoBrandingInitialized) {
    return;
  }
  window.__calcoBrandingInitialized = true;

  // Defaults + Grid Branding Settings, merged by calco_workspace_config.js.
  const SITE_CONFIG = window.calcoWorkspaceViewConfig || {};
  const BRAND = SITE_CONFIG.brand || {};
  const TEXT = SITE_CONFIG.text || {};
  const WORKSPACE_ORDER = SITE_CONFIG.workspaceOrder || [];
  const WORKSPACE_LOGO_OVERRIDES = SITE_CONFIG.workspaceLogoOverrides || {};
  const BRAND_LOGO_ICON_PATHS = SITE_CONFIG.brandLogoIconPaths || [];
  const FOLDER_LOGO_LABELS = SITE_CONFIG.folderLogoLabels || [];
  const APP_VERSION = SITE_CONFIG.appVersion || "";
  const AUTH_VIEW_SELECTOR = [
    ".for-login",
    ".for-email-login",
    ".for-forgot",
    ".for-login-with-email-link",
    ".for-signup",
  ].join(",");
  let scheduled = false;

  function applyBrandVariables() {
    const root = document.documentElement;
    root.style.setProperty("--calco-red", BRAND.primaryColor);
    root.style.setProperty("--calco-red-deep", BRAND.primaryDeepColor);
    root.style.setProperty("--calco-graphite", BRAND.headerColor);
    root.style.setProperty("--calco-ink", BRAND.textColor);
    root.style.setProperty("--calco-canvas", BRAND.pageColor);
    root.style.setProperty("--calco-home-header", BRAND.headerColor);
  }

  function brandText(key) {
    return BRAND[key];
  }

  function text(key) {
    return TEXT[key];
  }

  function textList(key) {
    return Array.isArray(TEXT[key]) ? TEXT[key] : [];
  }

  function isLoginPage() {
    return document.body?.classList.contains("for-login") || !!document.querySelector(".for-login");
  }

  function setFavicon() {
    let favicon = document.querySelector("link[rel='icon']");
    if (!favicon) {
      favicon = document.createElement("link");
      favicon.rel = "icon";
      document.head.appendChild(favicon);
    }
    const faviconUrl = brandText("faviconUrl");
    if (favicon.href !== new URL(faviconUrl, window.location.href).href) {
      favicon.href = faviconUrl;
      favicon.type = "image/svg+xml";
    }
  }

  function setDocumentTitle() {
    const isLogin = isLoginPage();
    const nextTitle = isLogin ? brandText("loginTitle") : brandText("erpTitle");
    if (document.title !== nextTitle) {
      document.title = nextTitle;
    }
  }

  function createBrandPanel() {
    const panel = document.createElement("aside");
    panel.className = "calco-login-brand-panel";
    panel.setAttribute("aria-label", brandText("appName"));
    panel.innerHTML = `
      <img class="calco-login-logo" src="${brandText("logoUrl")}" alt="${brandText("companyName")}">
      <h1 class="calco-login-company">${brandText("companyName")}</h1>
      <div class="calco-login-product">${brandText("appName")}</div>
      <p class="calco-login-tagline">${brandText("tagline")}</p>
      <p class="calco-login-capabilities">${brandText("capabilities")}</p>
    `;
    return panel;
  }

  function createLoginFooter() {
    const footer = document.createElement("div");
    footer.className = "calco-login-footer";
    footer.innerHTML = `
      <span>${brandText("companyName")} &middot; ${text("authorizedAccess")}</span>
      <span>${text("secureConnection")}</span>
    `;
    return footer;
  }

  function enhanceLoginViews() {
    if (!isLoginPage()) return;
    document.body.classList.add("calco-login-branded");

    const authViews = Array.from(document.querySelectorAll(AUTH_VIEW_SELECTOR));
    authViews.forEach((view) => view.classList.remove("calco-auth-active"));
    const activeView = authViews.find((view) => window.getComputedStyle(view).display !== "none");
    activeView?.classList.add("calco-auth-active");

    authViews.forEach((view) => {
      const loginCard = view.querySelector(":scope > .login-content.page-card");
      if (!loginCard) return;

      view.classList.add("calco-auth-view");
      if (!view.querySelector(":scope > .calco-login-brand-panel")) {
        view.prepend(createBrandPanel());
      }

      let formPanel = view.querySelector(":scope > .calco-login-form-panel");
      if (!formPanel) {
        formPanel = document.createElement("div");
        formPanel.className = "calco-login-form-panel";
        loginCard.before(formPanel);
        formPanel.appendChild(loginCard);
        formPanel.appendChild(createLoginFooter());
      }
    });

    const primaryHead = document.querySelector(".for-login .page-card-head");
    if (!primaryHead) return;

    const heading = primaryHead.querySelector("h4");
    if (heading) heading.textContent = text("loginHeading");

    let subtitle = primaryHead.querySelector(".page-card-subtitle, .calco-login-subtitle");
    if (!subtitle) {
      subtitle = document.createElement("p");
      primaryHead.appendChild(subtitle);
    }
    subtitle.classList.add("calco-login-subtitle");
    subtitle.textContent = text("loginSubtitlePrefix") + " " + brandText("appName");
  }

  function currentRoute() {
    if (window.frappe && typeof frappe.get_route === "function") {
      return (frappe.get_route() || []).map((part) => String(part || "").toLowerCase());
    }
    return [];
  }

  function isDeskHome() {
    if (isLoginPage()) return false;
    const route = currentRoute();
    const pathname = window.location.pathname.replace(/\/$/, "").toLowerCase();
    // The desk home also opens at the site root ("/") and at /app.
    const deskLoaded = !!(window.frappe && frappe.boot && frappe.boot.user);
    return (
      (deskLoaded && (pathname === "" || pathname === "/app")) ||
      pathname === "/desk" ||
      pathname === "/desk/desktop" ||
      pathname === "/app/desktop" ||
      route[0] === "desktop"
    );
  }

  function decorateDeskHeader() {
    if (isLoginPage()) return;
    const desktopNavbar =
      document.querySelector(".desktop-wrapper .desktop-navbar") ||
      document.querySelector(".desktop-navbar");
    if (desktopNavbar) {
      const nativeLogo = desktopNavbar.querySelector("#brand-logo");
      if (nativeLogo) {
        nativeLogo.src = brandText("logoUrl");
        nativeLogo.alt = brandText("companyName");
      }

      const nativeHome = desktopNavbar.querySelector(".navbar-home");
      if (nativeHome && !nativeHome.querySelector(".calco-desk-brand-copy")) {
        const copy = document.createElement("span");
        copy.className = "calco-desk-brand-copy";
        nativeHome.appendChild(copy);
      }

      const copy = nativeHome?.querySelector(".calco-desk-brand-copy");
      const home = isDeskHome();
      if (copy && copy.dataset.calcoSurface !== (home ? "home" : "standard")) {
        copy.innerHTML = home
          ? `
              <strong class="calco-desk-brand-label">${brandText("appName")}</strong>
              <small>${brandText("tagline")}</small>
              <span class="calco-home-nav-capabilities">${brandText("capabilities").replaceAll("•", "<i>&bull;</i>")}</span>
              <span class="calco-home-nav-values">${textList("bannerValues").join(" · ")}</span>
              <span class="calco-home-nav-description">${brandText("description")}</span>
            `
          : `
              <strong class="calco-desk-brand-label">${brandText("appName")}</strong>
              <small>${brandText("tagline")}</small>
            `;
        copy.dataset.calcoSurface = home ? "home" : "standard";
      }

      if (home) {
        desktopNavbar.classList.add("calco-home-unified-banner");
        let image = desktopNavbar.querySelector(":scope > .calco-home-navbar-image");
        if (!image) {
          image = document.createElement("img");
          image.className = "calco-home-navbar-image";
          image.src = brandText("bannerUrl");
          image.alt = "";
          image.setAttribute("aria-hidden", "true");
          desktopNavbar.prepend(image);
        }
      } else {
        desktopNavbar.classList.remove("calco-home-unified-banner");
        desktopNavbar.querySelector(":scope > .calco-home-navbar-image")?.remove();
      }
      return;
    }

    const navbar = document.querySelector(
      "header.navbar .container, header.navbar .container-fluid, .navbar .container"
    );
    if (!navbar || navbar.querySelector(":scope > .calco-desk-brand")) return;

    const brand = document.createElement("div");
    brand.className = "calco-desk-brand";
    brand.setAttribute("aria-label", brandText("appName"));
    brand.innerHTML = `
      <img src="${brandText("logoUrl")}" alt="${brandText("companyName")}">
      <span class="calco-desk-brand-copy">
        <strong class="calco-desk-brand-label">${brandText("appName")}</strong>
        <small>${brandText("tagline")}</small>
      </span>
    `;
    navbar.prepend(brand);
  }

  function createHomeIdentity() {
    const identity = document.createElement("section");
    identity.className = "calco-home-intro";
    identity.innerHTML = `
      <div class="calco-home-workspaces-bar">
        <h2>${text("workspaceHeading")}</h2>
      </div>
    `;
    return identity;
  }

  function createHomeFooter() {
    const footer = document.createElement("footer");
    footer.className = "calco-home-footer";
    footer.innerHTML = `
      <div class="calco-home-footer-left">
        <strong>${brandText("companyName")}</strong>
        <span>${text("authorizedAccess")}</span>
        <span class="calco-secure-connection">
          <svg class="icon icon-sm" aria-hidden="true"><use href="#icon-lock"></use></svg>
          ${text("secureConnection")}
        </span>
      </div>
      <div class="calco-home-footer-right">
        ${APP_VERSION ? `<span>v${APP_VERSION}</span>` : ""}
        <strong>${brandText("footerLine")}</strong>
      </div>
    `;
    return footer;
  }

  function createOperationalBrandbar() {
    const bar = document.createElement("div");
    bar.className = "calco-operational-brandbar";
    bar.setAttribute("aria-label", brandText("appName"));
    bar.innerHTML = `
      <img src="${brandText("logoUrl")}" alt="${brandText("companyName")}">
      <span class="calco-desk-brand-copy">
        <strong class="calco-desk-brand-label">${brandText("appName")}</strong>
        <small>${brandText("tagline")}</small>
      </span>
    `;
    return bar;
  }

  function workspaceTitle(icon) {
    return (
      icon.querySelector(".icon-title")?.textContent ||
      icon.getAttribute("title") ||
      ""
    ).trim();
  }

  function orderWorkspaceCards(iconsContainer, icons) {
    if (!WORKSPACE_ORDER.length) return;

    const order = new Map(WORKSPACE_ORDER.map((name, index) => [name.toLowerCase(), index]));
    icons
      .map((icon, index) => ({ icon, index, rank: order.get(workspaceTitle(icon).toLowerCase()) ?? 999 }))
      .sort((a, b) => a.rank - b.rank || a.index - b.index)
      .forEach(({ icon }) => iconsContainer.appendChild(icon));
  }

  function workspaceLogoUrl(title) {
    const configuredUrl = WORKSPACE_LOGO_OVERRIDES[title] || WORKSPACE_LOGO_OVERRIDES[title.toLowerCase()];
    if (!configuredUrl) return "";
    return configuredUrl === "__brand_logo__" ? brandText("logoUrl") : configuredUrl;
  }

  function applyBrandLogoToIcons() {
    if (!BRAND_LOGO_ICON_PATHS.length || isLoginPage()) return;
    const logoUrl = brandText("logoUrl");
    document.querySelectorAll(".desktop-icon img.app-icon").forEach((image) => {
      if (image.dataset.calcoBrandLogo === logoUrl) return;
      const source = image.dataset.calcoOriginalSrc || image.getAttribute("src") || "";
      if (!BRAND_LOGO_ICON_PATHS.some((path) => source.includes(path))) return;
      image.dataset.calcoOriginalSrc = source;
      image.dataset.calcoBrandLogo = logoUrl;
      image.src = logoUrl;
      image.classList.add("calco-workspace-logo-mark");
      image.parentElement?.classList.add("calco-workspace-logo-holder");
    });
  }

  // Frappe draws a folder as a thumbnail of its children; these folders get their own logo.
  // Runs everywhere the folder appears: desktop, folder pop-ups and nested thumbnails.
  function applyFolderLogos() {
    if (!FOLDER_LOGO_LABELS.length || !window.frappe?.utils?.get_desktop_icon || !frappe.boot) return;
    const style = frappe.boot.desktop_icon_style || "Solid";
    FOLDER_LOGO_LABELS.forEach((label) => {
      const logoUrl = frappe.utils.get_desktop_icon(label, style);
      if (!logoUrl) return;
      document.querySelectorAll(`.desktop-icon[data-id="${CSS.escape(label)}"]`).forEach((icon) => {
        const holder = icon.querySelector(":scope > .icon-container");
        if (!holder || holder.querySelector(":scope > img.calco-folder-logo")) return;
        holder.innerHTML = "";
        holder.classList.remove("folder-icon");
        const image = document.createElement("img");
        image.className = "app-icon calco-folder-logo";
        image.src = logoUrl;
        image.alt = label;
        holder.appendChild(image);
      });
    });
  }

  function applyWorkspaceLogoOverrides(icons) {
    icons.forEach((icon) => {
      const title = workspaceTitle(icon);
      const logoUrl = workspaceLogoUrl(title);
      if (!logoUrl) return;

      const holder = icon.querySelector(":scope > .icon-container");
      if (!holder || holder.dataset.calcoLogoUrl === logoUrl) return;

      holder.innerHTML = "";
      holder.dataset.calcoLogoUrl = logoUrl;
      holder.classList.add("calco-workspace-logo-holder");

      const image = document.createElement("img");
      image.className = "calco-workspace-logo-mark";
      image.src = logoUrl;
      image.alt = title;
      holder.appendChild(image);
    });
  }

  function syncOperationalBrandbar() {
    if (isLoginPage() || isDeskHome()) {
      document.querySelectorAll(".calco-operational-brandbar").forEach((bar) => bar.remove());
      return;
    }

    const activePage = Array.from(document.querySelectorAll(".page-container"))
      .find((page) => page.offsetParent !== null);
    if (!activePage) return;

    const activeBar = activePage.querySelector(":scope > .calco-operational-brandbar");
    document.querySelectorAll(".calco-operational-brandbar").forEach((bar) => {
      if (bar !== activeBar) bar.remove();
    });
    if (!activeBar) {
      activePage.prepend(createOperationalBrandbar());
    }
  }

  function syncWorkspaceCards() {
    if (!isDeskHome()) {
      document
        .querySelectorAll(".calco-home-intro, .calco-home-footer")
        .forEach((element) => element.remove());
      document.body?.classList.remove("calco-desk-home");
      return;
    }

    const wrapper =
      document.querySelector(".desktop-wrapper") ||
      document.querySelector("#page-desktop") ||
      document.querySelector(".layout-main-section") ||
      document.body;
    const desktopContainer =
      wrapper.querySelector(".desktop-container") ||
      document.querySelector(".desktop-container") ||
      wrapper;
    const iconsContainer =
      desktopContainer.querySelector(".icons-container > .icons") ||
      desktopContainer.querySelector(".icons") ||
      document.querySelector(".icons-container > .icons, .icons");
    const icons = Array.from(iconsContainer?.querySelectorAll(":scope > .desktop-icon") || []);
    if (!wrapper || !desktopContainer || !icons.length || !iconsContainer) return;

    document.body.classList.add("calco-desk-home");
    wrapper.classList.add("calco-desktop-branded");
    document.querySelectorAll(".calco-operational-brandbar").forEach((bar) => bar.remove());
    orderWorkspaceCards(iconsContainer, icons);
    applyWorkspaceLogoOverrides(icons);
    let existing = wrapper.querySelector(":scope > .calco-home-intro");
    document.querySelectorAll(".calco-home-intro").forEach((element) => {
      if (element !== existing) element.remove();
    });
    if (!existing) {
      existing = createHomeIdentity();
      desktopContainer.before(existing);
    }

    let footer = wrapper.querySelector(":scope > .calco-home-footer");
    document.querySelectorAll(".calco-home-footer").forEach((element) => {
      if (element !== footer) element.remove();
    });
    if (!footer) {
      wrapper.appendChild(createHomeFooter());
    }
  }


  function applyBranding() {
    scheduled = false;
    applyBrandVariables();
    setFavicon();
    setDocumentTitle();
    enhanceLoginViews();
    decorateDeskHeader();
    syncWorkspaceCards();
    applyBrandLogoToIcons();
    applyFolderLogos();
    syncOperationalBrandbar();
  }

  function scheduleApply() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(applyBranding);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scheduleApply, { once: true });
  } else {
    scheduleApply();
  }

  window.addEventListener("load", scheduleApply, { once: true });
  window.addEventListener("hashchange", scheduleApply);
  document.addEventListener("page-change", scheduleApply);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      scheduleApply();
    }
  });

  if (window.frappe && frappe.router && typeof frappe.router.on === "function") {
    frappe.router.on("change", scheduleApply);
  }

  if (document.body && window.MutationObserver) {
    new MutationObserver(scheduleApply).observe(document.body, { childList: true, subtree: true });
  }
})();
