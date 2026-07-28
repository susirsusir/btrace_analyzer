# Chinese Output Rules - 中文输出规则

## Rule Definition - 规则定义

**Rule Name**: `prefer_chinese_output`  
**Description**: When the user communicates in Chinese, respond in Chinese.  
**描述**: 当用户使用中文交流时，以中文响应。

## Rules Details - 规则详情

### Primary Rule - 主要规则
- If the user's input contains Chinese characters (including mixed Chinese-English text), the response should be primarily in Chinese.
- 如果用户输入包含中文字符（包括中英混合文本），回复应以中文为主。

### Secondary Rule - 补充规则
- Maintain language consistency throughout the conversation: once started in Chinese, continue in Chinese unless explicitly switched by the user.
- 保持会话语言一致性：一旦以中文开始，除非用户明确切换，否则继续以中文进行。

### Technical Implementation - 技术实现
This rule is enforced via:
1. `.claude/settings.local.json`: `"language": "chinese"` sets the preferred language
2. Project documentation and guidelines reference this setting

该规则通过以下途径实施：
1. `.claude/settings.local.json`: `"language": "chinese"` 设置首选语言
2. 项目文档和指南引用此设置

## Related Files - 相关文件

- `.claude/settings.local.json` - Language preference configuration
- `.claude/rules/chinese-output-rules.md` - This rule definition file
- `README_CN.md` - Chinese version of project documentation

## Verification - 验证方法
The Claude Code settings loader will apply this language preference on session start. The assistant should respect this setting and respond in Chinese when appropriate.

Claude Code 设置加载器将在会话启动时应用此语言偏好设置。助手应尊重此设置并在适当时使用中文响应。
