/* 只翻译显示文字；不修改示例 JSON、请求内容、路径或真实响应。 */
(() => {
  "use strict";
  const words = {
    "Try it out": "试用接口", "Execute": "发送请求", "Executing": "正在请求",
    "Clear": "清空结果", "Cancel": "取消", "Reset": "重置",
    "Parameters": "参数", "No parameters": "无需参数", "Name": "名称",
    "Description": "说明", "Request body": "请求内容", "required": "必填",
    "Required": "必填", "* required": "* 必填", "Responses": "响应结果",
    "Response": "响应", "Server response": "服务器响应", "Code": "状态码",
    "Details": "详情", "Response body": "响应内容", "Response headers": "响应头",
    "Request URL": "请求地址", "Example Value": "示例内容", "Schema": "字段说明",
    "Schemas": "数据结构", "Models": "数据结构", "Model": "结构",
    "Media type": "数据格式", "Media Type": "数据格式", "Links": "关联链接",
    "No links": "无关联链接", "Successful Response": "请求成功",
    "Validation Error": "参数校验错误", "Expand all": "全部展开",
    "Collapse all": "全部收起", "Expand operation": "展开接口", "Collapse operation": "收起接口",
    "Copy path to clipboard": "复制接口地址", "Copy to clipboard": "复制内容",
    "Copy": "复制", "Copied": "已复制", "Download": "下载",
    "Copy cURL command to clipboard": "复制命令行示例", "Copy code to clipboard": "复制响应内容",
    "Download response body": "下载响应内容", "Curl": "命令行示例", "ms": "毫秒",
    "Download file": "下载文件", "Request duration": "请求耗时",
    "Response content type": "响应数据格式", "Request content type": "请求数据格式",
    "Edit Value": "编辑内容", "Example": "示例", "Examples": "示例",
    "Available values": "可选值", "Default value": "默认值", "Send empty value": "发送空值",
    "Undocumented": "未单独列出", "Error": "错误", "Controls": "设置", "header.": "请求头。",
    "HTTPValidationError": "请求参数校验错误", "ValidationError": "参数错误详情",
    "ImageUploaded": "图片上传结果", "TokenUsage": "模型用量",
    "VisionAnswer": "图片理解结果", "VisionRequest": "图片理解请求",
    "VisionStatus": "图片理解配置", "ProjectInfo": "项目信息", "HealthStatus": "健康状态",
    "VideoInfo": "视频上传结果", "VideoAnswer": "视频观察结果", "VideoStatus": "视频配置",
    "SampleFrame": "采样画面", "Observation": "观察记录", "AnalyzeRequest": "视频分析请求",
    "video_id": "video_id（视频编号）", "video_url": "video_url（播放地址）",
    "duration_seconds": "duration_seconds（视频时长）", "sample_count": "sample_count（采样次数）",
    "sample_interval_seconds": "sample_interval_seconds（采样间隔）", "frames": "frames（采样画面）",
    "frame_index": "frame_index（依据采样编号）", "index": "index（采样编号）",
    "time_seconds": "time_seconds（采样秒数）", "seen_at": "seen_at（相同画面的采样时间）",
    "observations": "observations（观察记录）", "keywords": "keywords（主要事物）",
    "uncertain": "uncertain（是否待确认）", "summary": "summary（视频概述）",
    "reply": "reply（小灵回应）", "cached": "cached（是否复用结果）", "result": "result（已有结果）",
    "video_context": "video_context（本话题视频观察与首次分享）", "kind": "kind（话题类型）",
    "max_bytes": "max_bytes（文件大小上限）", "max_seconds": "max_seconds（时长上限）",
    "max_frames": "max_frames（采样上限）", "Body_upload_api_videos_post": "视频上传请求",
    "Body_upload_image_api_images_post": "图片上传请求",
    "SendMessage": "发送伙伴消息", "ChatMessage": "对话消息", "ChatTurn": "伙伴回复",
    "RoleProfile": "小灵人格设定", "SaveCard": "保存人格卡", "BuildCard": "讨论人格草稿",
    "Preview": "角色预览", "Extract": "整理话题记忆", "AddMemory": "手动添加记忆", "EditMemory": "编辑记忆",
    "profile": "profile（人格设定）", "expected": "expected（当前资料版本）",
    "identity": "identity（身份定位）", "background": "background（背景设定）",
    "traits": "traits（核心性格）", "speaking_style": "speaking_style（说话方式）",
    "interests": "interests（兴趣与审美）", "relationship": "relationship（相处方式）",
    "avoidances": "avoidances（减少的表达）", "category": "category（记忆类型）",
    "memory_id": "memory_id（记忆条目编号）",
    "definition": "definition（自由人物设定）", "mode": "mode（设定形式）",
    "conversation_id": "conversation_id（话题编号）", "request_id": "request_id（消息请求编号）",
    "message": "message（你的消息）", "content": "content（消息内容）", "role": "role（发言者）",
    "created_at": "created_at（创建时间）", "persona_id": "persona_id（固定人格编号）",
    "user_message": "user_message（用户消息）", "assistant_message": "assistant_message（伙伴回复）",
    "replayed": "replayed（是否恢复已有回复）",
    "file": "file（上传文件）", "image_id": "image_id（图片编号）",
    "question": "question（提问内容）", "answer": "answer（理解回复）",
    "model": "model（使用模型）", "usage": "usage（模型用量）",
    "truncated": "truncated（回复是否被截断）", "configured": "configured（是否已填密钥）",
    "image_detail": "image_detail（图片精度）", "max_tokens": "max_tokens（输出上限）",
    "media_type": "media_type（图片格式）", "size_bytes": "size_bytes（文件字节数）",
    "width": "width（图片宽度）", "height": "height（图片高度）", "url": "url（预览地址）",
    "prompt_tokens": "prompt_tokens（输入用量）", "completion_tokens": "completion_tokens（输出用量）",
    "total_tokens": "total_tokens（总用量）", "project": "project（项目名称）",
    "version": "version（版本）", "status": "status（运行状态）", "docs": "docs（文档地址）",
    "uptime_seconds": "uptime_seconds（已运行秒数）", "timestamp": "timestamp（检查时间）",
    "detail": "detail（错误详情）", "loc": "loc（错误位置）", "msg": "msg（错误信息）",
    "type": "type（类型）", "string": "文本", "integer": "整数", "number": "数字",
    "boolean": "布尔值", "object": "对象", "array": "列表", "binary": "文件内容",
    "(path)": "（路径参数）", "(query)": "（查询参数）", "(body)": "（请求内容）",
  };
  const root = document.getElementById("swagger-ui");
  if (!root) return;
  const skip = "pre,code,textarea,input,select,option,script,style,.highlight-code,.microlight,.vs-file-picker";

  function translate(value) {
    const trimmed = value.trim();
    if (Object.hasOwn(words, trimmed)) return value.replace(trimmed, words[trimmed]);
    if (/^Error: response status is \d+$/.test(trimmed)) return "请求失败，状态码：" + trimmed.match(/\d+$/)[0];
    return value;
  }

  function filePickers() {
    root.querySelectorAll(".vs-file-picker").forEach(picker => {
      if (!picker.nextElementSibling?.matches('input[type="file"]')) picker.remove();
    });
    root.querySelectorAll('input[type="file"]').forEach(input => {
      let picker = input.previousElementSibling;
      if (!picker?.classList.contains("vs-file-picker")) {
        picker = document.createElement("div");
        picker.className = "vs-file-picker";
        const button = document.createElement("button");
        button.type = "button";
        button.className = "btn";
        button.textContent = "选择文件";
        button.addEventListener("click", () => input.click());
        const name = document.createElement("span");
        name.setAttribute("aria-live", "polite");
        const update = () => {
          name.textContent = input.files?.length ? Array.from(input.files, f => f.name).join("、") : "尚未选择文件";
          button.textContent = input.files?.length ? "更换文件" : "选择文件";
        };
        input.addEventListener("change", update);
        picker.append(button, name);
        input.before(picker);
        input.classList.add("vs-native-file");
        input.setAttribute("aria-label", "上传文件");
        input.setAttribute("aria-hidden", "true");
        input.setAttribute("tabindex", "-1");
        update();
      }
      picker.querySelector("button").disabled = input.disabled;
    });
  }

  let queued = false;
  const observer = new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(localize);
  });
  function localize() {
    observer.disconnect();
    queued = false;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(node => {
      if (node.parentElement && (!node.parentElement.closest(skip) || node.parentElement.closest("button"))) {
        const changed = translate(node.nodeValue);
        if (changed !== node.nodeValue) node.nodeValue = changed;
      }
    });
    root.querySelectorAll("[title],[aria-label],[placeholder]").forEach(element => {
      for (const attribute of ["title", "aria-label", "placeholder"]) {
        const value = element.getAttribute(attribute);
        if (value) {
          const changed = translate(value);
          if (changed !== value) element.setAttribute(attribute, changed);
        }
      }
    });
    filePickers();
    observer.observe(root, {subtree: true, childList: true, characterData: true, attributes: true,
      attributeFilter: ["title", "aria-label", "placeholder", "disabled"]});
  }
  localize();
})();
