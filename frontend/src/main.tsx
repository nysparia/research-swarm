import React from 'react';
import ReactDOM from 'react-dom/client';
import { App as AntdApp, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import 'antd/dist/reset.css';
import './conversation.css';
import './workbench/workbench.css';
import App from './App';
import './workbench/macos.css';
import './workbench/native-controls.css';
import './workbench/glass-system.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} button={{ autoInsertSpace: false }} theme={{
      token: {
        colorPrimary: '#007aff',
        colorText: '#1d1d1f',
        colorTextSecondary: '#63636a',
        colorBgLayout: '#f5f5f7',
        colorBorder: '#dcdce1',
        borderRadius: 6,
        fontSize: 13,
        fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Segoe UI", "Microsoft YaHei", sans-serif',
        controlHeight: 28,
      },
      components: {
        Button: { primaryShadow: '0 1px 2px #007aff20' },
        Modal: { borderRadiusLG: 20 },
        Drawer: { colorBgElevated: '#fbfbfd' },
        Tabs: { horizontalMargin: '0 0 20px 0' },
      },
    }}>
      <AntdApp>
        <App />
      </AntdApp>
    </ConfigProvider>
  </React.StrictMode>,
);
