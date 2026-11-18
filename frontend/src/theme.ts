import type { ThemeConfig } from 'antd'

export const workbenchTheme: ThemeConfig = {
  token: {
    colorPrimary: '#1d4ed8', colorSuccess: '#18794e', colorWarning: '#b86b12', colorError: '#b42318',
    colorText: '#17243b', colorTextSecondary: '#647188', colorBorder: '#d7dee9',
    colorBgLayout: '#f5f7fa', borderRadius: 5, fontSize: 12, controlHeight: 32,
    fontFamily: '"Segoe UI Variable", "Noto Sans SC", "Microsoft YaHei UI", sans-serif',
  },
  components: { Button: { primaryShadow: 'none' }, Tabs: { horizontalMargin: '0' }, Table: { headerBg: '#f5f7fa' } },
}
