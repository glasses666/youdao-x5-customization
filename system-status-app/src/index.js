import systemInfoModule from "systemInfo";
import batteryInfoModule from "batteryInfo";

const systemInfo = new systemInfoModule.SystemInfo();
const batteryInfo = new batteryInfoModule.BatteryInfo();

function readValue(reader, fallback) {
  try {
    const value = reader();
    return value === undefined || value === null || value === "" ? fallback : value;
  } catch (error) {
    console.log("system-status read failed", error.message);
    return fallback;
  }
}

function card(create, text, label, value, width) {
  return create("div", { staticClass: ["card"], staticStyle: { width: width } }, [
    create("text", { staticClass: ["label"] }, [text(label)]),
    create("text", { staticClass: ["value"] }, [text(value)])
  ]);
}

const StatusView = {
  name: "system-status",
  data: function () {
    return {
      battery: "--",
      storage: "--",
      model: "--",
      version: "--",
      wlan: "--",
      refreshCount: 0
    };
  },
  mounted: function () {
    this.refresh();
  },
  methods: {
    refresh: function () {
      const capacity = readValue(function () { return batteryInfo.getBatteryCapacity(); }, "--");
      const charging = readValue(function () { return batteryInfo.getBatteryStatus(); }, false);
      const storage = readValue(function () { return JSON.parse(systemInfo.getStorageInfo()); }, null);

      this.battery = capacity + "% · " + (charging ? "充电" : "供电");
      this.storage = storage
        ? ((Number(storage.user) + Number(storage.resource) + Number(storage.firmware)) / 1024).toFixed(1)
          + " / " + storage.totalSpace + " GB"
        : "读取失败";
      this.model = String(readValue(function () { return systemInfo.getDeviceType(); }, "未知"));
      this.version = String(readValue(function () { return systemInfo.getVersion(); }, "未知"));
      this.wlan = String(readValue(function () { return systemInfo.getMacInfo(); }, "未连接"));
      this.refreshCount += 1;
    }
  }
};

function render() {
  const create = this._self._c || this.$createElement;
  const text = this._v;
  return create("div", { staticClass: ["wrapper"] }, [
    create("div", { staticClass: ["header"] }, [
      create("text", { staticClass: ["title"] }, [text("系统状态")]),
      create("text", { staticClass: ["network"] }, [text("WLAN " + this.wlan)]),
      create("text", { staticClass: ["refresh"], on: { click: this.refresh } }, [
        text("刷新 · " + this.refreshCount)
      ])
    ]),
    create("div", { staticClass: ["cards"] }, [
      card(create, text, "电池", this.battery, "170px"),
      card(create, text, "存储", this.storage, "210px"),
      card(create, text, "平台", this.model, "145px"),
      card(create, text, "系统版本", this.version, "205px")
    ])
  ]);
}

render._withStripped = true;
StatusView.render = render;
StatusView.staticRenderFns = [];
StatusView._compiled = true;
StatusView.themes = {};
StatusView.style = {
  wrapper: {
    width: "800px",
    height: "254px",
    backgroundColor: "#10151f",
    flexDirection: "column"
  },
  header: {
    width: "768px",
    height: "50px",
    marginLeft: "16px",
    marginTop: "10px",
    flexDirection: "row",
    alignItems: "center"
  },
  title: {
    width: "150px",
    color: "#f5f7fa",
    fontSize: "28px",
    fontWeight: "500"
  },
  network: {
    width: "470px",
    color: "#8d9bad",
    fontSize: "20px"
  },
  refresh: {
    width: "148px",
    height: "42px",
    lineHeight: "42px",
    textAlign: "center",
    color: "#ffffff",
    backgroundColor: "#15817c",
    borderRadius: "21px",
    fontSize: "21px"
  },
  cards: {
    width: "768px",
    height: "174px",
    marginLeft: "16px",
    marginTop: "10px",
    flexDirection: "row",
    gap: "10px"
  },
  card: {
    height: "164px",
    paddingLeft: "14px",
    paddingRight: "14px",
    paddingTop: "24px",
    backgroundColor: "#1b2430",
    borderRadius: "16px",
    flexDirection: "column"
  },
  label: {
    color: "#8d9bad",
    fontSize: "21px",
    lineHeight: "28px"
  },
  value: {
    marginTop: "20px",
    color: "#f5f7fa",
    fontSize: "24px",
    lineHeight: "32px"
  }
};
StatusView.__file = "src/pages/index/index.vue";

export default class StatusPage extends $falcon.Page {
  onLoad(options) {
    super.onLoad(options);
    this.setRootComponent(StatusView);
  }
}
