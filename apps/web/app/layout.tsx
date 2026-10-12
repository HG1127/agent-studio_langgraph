import "./globals.css";
import "./runtime.css";
import "./auth.css";
import "./models.css";
export const metadata = {
  title: "Agent Studio | 创建真正会工作的 AI 助手",
  description: "自然语言创建、真实执行并管理 AI Agent。本地开发预览。",
};
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
