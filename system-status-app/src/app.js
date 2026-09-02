class BasePage extends $falcon.Page {}

class StatusApp extends $falcon.App {
  onLaunch(options) {
    super.onLaunch(options);
    $falcon.useDefaultBasePageClass(BasePage);
  }
}

StatusApp.meta = {
  pages: { index: "pages/index/index.js" },
  options: { style: { lessPaths: ["styles"] } },
  services: {},
  meta: { otaVersion: "3.4.0" },
  props: {
    supportUnInstall: true,
    addDesktop: {
      coco_platform: true,
      almond_platform: false,
      x3s_platform: false,
      apollo_platform: false,
      plum_platform: false,
      melon_platform: false
    }
  }
};
StatusApp.meta.name = "system_status";
StatusApp.meta.version = "1.0.1";
StatusApp.meta.isSingleJsBundle = false;

$falcon.__AppClazz = StatusApp;
$falcon.__loadModuleDefault = async function (path) {
  try {
    return (await import("./" + path + ".js")).default;
  } catch (error) {
    console.log("system-status load failed", error.message, error.stack);
  }
};
