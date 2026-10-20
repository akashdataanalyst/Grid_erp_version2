(function () {
  // Built-in defaults. Anything filled in on the "Grid Branding Settings" page
  // (desk: frappe.boot, login/website: inline script) is merged over these below.
  const config = {
    appVersion: "1.1.0 RC1",
    brand: {
      companyName: "Calco PolyTechnik Pvt Ltd",
      appName: "Manufacturing ERP",
      loginTitle: "Calco PolyTechnik Pvt Ltd Manufacturing ERP",
      erpTitle: "Calco PolyTechnik Pvt Ltd ERP",
      tagline: "Engineering Excellence. Digitally Controlled.",
      capabilities: "Manufacturing • Quality • Traceability",
      description: "Integrated manufacturing, quality and supply-chain operations.",
      footerLine: "Engineering Tomorrow. Together.",
      logoUrl: "/api/method/grid_erp.assets.serve?path=images/calco-logo-official.svg",
      faviconUrl: "/api/method/grid_erp.assets.serve?path=images/calco-polytechnik-favicon.svg",
      bannerUrl: "/api/method/grid_erp.assets.serve?path=images/calco-polymer-pellets-banner.png",
      primaryColor: "#b32025",
      primaryDeepColor: "#8f1a1e",
      headerColor: "#25282d",
      textColor: "#36393e",
      pageColor: "#fbfbfb",
    },
    text: {
      loginHeading: "Welcome back",
      loginSubtitlePrefix: "Sign in to",
      authorizedAccess: "Authorized Access Only",
      secureConnection: "Secure connection · HTTPS",
      workspaceHeading: "Your Workspaces",
      bannerValues: ["Precision", "People", "Progress"],
    },
    // Desktop icons (including those inside folder pop-ups) whose image comes from
    // one of these paths show the brand logo instead.
    brandLogoIconPaths: ["/assets/hrms/"],
    workspaceLogoOverrides: {
      "Frappe HR": "__brand_logo__",
    },
    workspaceOrder: [],
    // Desktop folders (icon-grid desk mode) that show their own logo
    // (public/icons/desktop_icons/<variant>/<label>.svg) instead of a thumbnail of the icons inside.
    // grid_erp ships none since its modules moved to the "Grid ERP" apps tile (see desktop.py).
    folderLogoLabels: [],
  };

  const settings =
    (window.frappe && frappe.boot && frappe.boot.calco_branding) || window.calcoBrandingSettings || {};
  window.calcoWorkspaceViewConfig = {
    ...config,
    ...settings,
    brand: { ...config.brand, ...(settings.brand || {}) },
    text: { ...config.text, ...(settings.text || {}) },
  };
})();
