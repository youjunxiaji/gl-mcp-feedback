/**
 * MCP Feedback Enhanced - 會話管理模組（重構版）
 * =============================================
 *
 * 整合會話數據管理、UI 渲染和面板控制功能
 * 使用模組化架構提升可維護性
 */

(function() {
    'use strict';

    // 確保命名空間和依賴存在
    window.MCPFeedback = window.MCPFeedback || {};

    // 獲取 DOMUtils 的安全方法
    function getDOMUtils() {
        return window.MCPFeedback && window.MCPFeedback.Utils && window.MCPFeedback.Utils.DOM;
    }

    /**
     * 會話管理器建構函數（重構版）
     */
    function SessionManager(options) {
        options = options || {};

        // 子模組實例
        this.dataManager = null;
        this.uiRenderer = null;
        this.detailsModal = null;

        // UI 狀態
        this.isLoading = false;

        // 設定管理器引用
        this.settingsManager = options.settingsManager || null;

        // 回調函數
        this.onSessionChange = options.onSessionChange || null;
        this.onSessionSelect = options.onSessionSelect || null;

        this.initializeModules(options);
        this.setupEventListeners();

        console.log('📋 SessionManager (重構版) 初始化完成');
    }

    /**
     * 初始化子模組
     */
    SessionManager.prototype.initializeModules = function(options) {
        const self = this;

        // 先初始化 UI 渲染器（避免數據管理器回調時 UI 組件尚未準備好）
        this.uiRenderer = new window.MCPFeedback.Session.UIRenderer({
            showFullSessionId: options.showFullSessionId || false,
            enableAnimations: options.enableAnimations !== false
        });

        // 初始化詳情彈窗
        this.detailsModal = new window.MCPFeedback.Session.DetailsModal({
            enableEscapeClose: options.enableEscapeClose !== false,
            enableBackdropClose: options.enableBackdropClose !== false,
            showFullSessionId: options.showFullSessionId || false
        });

        // 初始化防抖處理器
        this.initDebounceHandlers();

        // 最後初始化數據管理器（確保 UI 組件已準備好接收回調）
        this.dataManager = new window.MCPFeedback.Session.DataManager({
            settingsManager: this.settingsManager,
            onSessionChange: function(sessionData) {
                self.handleSessionChange(sessionData);
            },
            onHistoryChange: function(history) {
                self.handleHistoryChange(history);
            },
            onStatsChange: function(stats) {
                self.handleStatsChange(stats);
            },
            onDataChanged: function() {
                self.handleDataChanged();
            }
        });
    };

    /**
     * 初始化防抖處理器
     */
    SessionManager.prototype.initDebounceHandlers = function() {
        // 為會話變更處理添加防抖
        this._debouncedHandleSessionChange = window.MCPFeedback.Utils.DOM.debounce(
            this._originalHandleSessionChange.bind(this),
            100,
            false
        );

        // 為歷史記錄變更處理添加防抖
        this._debouncedHandleHistoryChange = window.MCPFeedback.Utils.DOM.debounce(
            this._originalHandleHistoryChange.bind(this),
            150,
            false
        );

        // 為統計資訊變更處理添加防抖
        this._debouncedHandleStatsChange = window.MCPFeedback.Utils.DOM.debounce(
            this._originalHandleStatsChange.bind(this),
            100,
            false
        );

        // 為資料變更處理添加防抖
        this._debouncedHandleDataChanged = window.MCPFeedback.Utils.DOM.debounce(
            this._originalHandleDataChanged.bind(this),
            200,
            false
        );
    };

    /**
     * 處理會話變更（原始版本，供防抖使用）
     */
    SessionManager.prototype._originalHandleSessionChange = function(sessionData) {
        // 減少重複日誌：只在會話 ID 變化時記錄
        const sessionId = sessionData ? sessionData.session_id : null;
        if (!this._lastSessionId || this._lastSessionId !== sessionId) {
            console.log('📋 處理會話變更:', sessionData);
            this._lastSessionId = sessionId;
        }

        // 更新 UI 渲染
        this.uiRenderer.renderCurrentSession(sessionData);

        // 調用外部回調
        if (this.onSessionChange) {
            this.onSessionChange(sessionData);
        }
    };

    /**
     * 處理會話變更（防抖版本）
     */
    SessionManager.prototype.handleSessionChange = function(sessionData) {
        if (this._debouncedHandleSessionChange) {
            this._debouncedHandleSessionChange(sessionData);
        } else {
            // 回退到原始方法（防抖未初始化時）
            this._originalHandleSessionChange(sessionData);
        }
    };

    /**
     * 處理歷史記錄變更（原始版本，供防抖使用）
     */
    SessionManager.prototype._originalHandleHistoryChange = function(history) {
        // 減少重複日誌：只在歷史記錄數量變化時記錄
        if (!this._lastHistoryCount || this._lastHistoryCount !== history.length) {
            console.log('📋 處理歷史記錄變更:', history.length, '個會話');
            this._lastHistoryCount = history.length;
        }

        // 更新 UI 渲染
        this.uiRenderer.renderSessionHistory(history);
    };

    /**
     * 處理歷史記錄變更（防抖版本）
     */
    SessionManager.prototype.handleHistoryChange = function(history) {
        if (this._debouncedHandleHistoryChange) {
            this._debouncedHandleHistoryChange(history);
        } else {
            // 回退到原始方法（防抖未初始化時）
            this._originalHandleHistoryChange(history);
        }
    };

    /**
     * 處理統計資訊變更（原始版本，供防抖使用）
     */
    SessionManager.prototype._originalHandleStatsChange = function(stats) {
        // 減少重複日誌：只在統計資訊有意義變化時記錄
        const statsKey = stats ? JSON.stringify(stats) : null;
        if (!this._lastStatsKey || this._lastStatsKey !== statsKey) {
            console.log('📋 處理統計資訊變更:', stats);
            this._lastStatsKey = statsKey;
        }

        // 更新 UI 渲染
        this.uiRenderer.renderStats(stats);
    };

    /**
     * 處理統計資訊變更（防抖版本）
     */
    SessionManager.prototype.handleStatsChange = function(stats) {
        if (this._debouncedHandleStatsChange) {
            this._debouncedHandleStatsChange(stats);
        } else {
            // 回退到原始方法（防抖未初始化時）
            this._originalHandleStatsChange(stats);
        }
    };

    /**
     * 處理資料變更（原始版本，供防抖使用）
     */
    SessionManager.prototype._originalHandleDataChanged = function() {
        console.log('📋 處理資料變更，重新渲染所有內容');

        // 重新渲染所有內容
        const currentSession = this.dataManager.getCurrentSession();
        const history = this.dataManager.getSessionHistory();
        const stats = this.dataManager.getStats();

        this.uiRenderer.renderCurrentSession(currentSession);
        this.uiRenderer.renderSessionHistory(history);
        this.uiRenderer.renderStats(stats);
    };

    /**
     * 處理資料變更（防抖版本）
     */
    SessionManager.prototype.handleDataChanged = function() {
        if (this._debouncedHandleDataChanged) {
            this._debouncedHandleDataChanged();
        } else {
            // 回退到原始方法（防抖未初始化時）
            this._originalHandleDataChanged();
        }
    };

    /**
     * 設置事件監聽器
     */
    SessionManager.prototype.setupEventListeners = function() {
        const self = this;
        const DOMUtils = getDOMUtils();



        // 刷新按鈕
        const refreshButton = DOMUtils ?
            DOMUtils.safeQuerySelector('#refreshSessions') :
            document.querySelector('#refreshSessions');
        if (refreshButton) {
            refreshButton.addEventListener('click', function() {
                self.refreshSessionData();
            });
        }

        // 詳細資訊按鈕
        const detailsButton = DOMUtils ?
            DOMUtils.safeQuerySelector('#viewSessionDetails') :
            document.querySelector('#viewSessionDetails');
        if (detailsButton) {
            detailsButton.addEventListener('click', function() {
                self.showSessionDetails();
            });
        }

        // 复制当前会话内容按钮
        const copySessionButton = DOMUtils ?
            DOMUtils.safeQuerySelector('#copyCurrentSessionContent') :
            document.querySelector('#copyCurrentSessionContent');
        if (copySessionButton) {
            copySessionButton.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                self.copyCurrentSessionContent();
            });
        }

        // 會話歷史管理按鈕 - 會話管理頁籤
        // 匯出全部按鈕
        const sessionTabExportAllBtn = DOMUtils ?
            DOMUtils.safeQuerySelector('#sessionTabExportAllBtn') :
            document.querySelector('#sessionTabExportAllBtn');
        if (sessionTabExportAllBtn) {
            sessionTabExportAllBtn.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                self.exportSessionHistory();
            });
        }

        // 清空訊息記錄按鈕
        const sessionTabClearMessagesBtn = DOMUtils ?
            DOMUtils.safeQuerySelector('#sessionTabClearMessagesBtn') :
            document.querySelector('#sessionTabClearMessagesBtn');
        if (sessionTabClearMessagesBtn) {
            sessionTabClearMessagesBtn.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                self.clearUserMessages();
            });
        }

        // 清空所有會話按鈕
        const sessionTabClearAllBtn = DOMUtils ?
            DOMUtils.safeQuerySelector('#sessionTabClearAllBtn') :
            document.querySelector('#sessionTabClearAllBtn');
        if (sessionTabClearAllBtn) {
            sessionTabClearAllBtn.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                self.clearSessionHistory();
            });
        }
    };

    /**
     * 更新當前會話（委託給數據管理器）
     */
    SessionManager.prototype.updateCurrentSession = function(sessionData) {
        return this.dataManager.updateCurrentSession(sessionData);
    };

    /**
     * 更新狀態資訊（委託給數據管理器）
     */
    SessionManager.prototype.updateStatusInfo = function(statusInfo) {
        return this.dataManager.updateStatusInfo(statusInfo);
    };












    /**
     * 刷新會話數據
     */
    SessionManager.prototype.refreshSessionData = function() {
        if (this.isLoading) return;

        console.log('📋 刷新會話數據');
        this.isLoading = true;

        const self = this;
        // 這裡可以發送 WebSocket 請求獲取最新數據
        setTimeout(function() {
            self.isLoading = false;
            console.log('📋 會話數據刷新完成');
        }, 1000);
    };

    /**
     * 顯示當前會話詳情
     */
    SessionManager.prototype.showSessionDetails = function() {
        const currentSession = this.dataManager.getCurrentSession();

        if (!currentSession) {
            const message = window.i18nManager ? 
                window.i18nManager.t('sessionHistory.noActiveSession', '目前沒有活躍的會話數據') : 
                '目前沒有活躍的會話數據';
            this.showMessage(message, 'warning');
            return;
        }

        this.detailsModal.showSessionDetails(currentSession);
    };



    /**
     * 查看會話詳情（通過會話ID）
     */
    SessionManager.prototype.viewSessionDetails = function(sessionId) {
        console.log('📋 查看會話詳情:', sessionId);

        const sessionData = this.dataManager.findSessionById(sessionId);

        if (sessionData) {
            this.detailsModal.showSessionDetails(sessionData);
        } else {
            const message = window.i18nManager ? 
                window.i18nManager.t('sessionHistory.sessionNotFound', '找不到會話資料') : 
                '找不到會話資料';
            this.showMessage(message, 'error');
        }
    };



    /**
     * 獲取當前會話（便利方法）
     */
    SessionManager.prototype.getCurrentSession = function() {
        return this.dataManager.getCurrentSession();
    };

    /**
     * 獲取會話歷史（便利方法）
     */
    SessionManager.prototype.getSessionHistory = function() {
        return this.dataManager.getSessionHistory();
    };

    /**
     * 獲取統計資訊（便利方法）
     */
    SessionManager.prototype.getStats = function() {
        return this.dataManager.getStats();
    };

    /**
     * 獲取當前會話數據（相容性方法）
     */
    SessionManager.prototype.getCurrentSessionData = function() {
        console.log('📋 嘗試獲取當前會話數據...');

        const currentSession = this.dataManager.getCurrentSession();

        if (currentSession && currentSession.session_id) {
            console.log('📋 從 dataManager 獲取數據:', currentSession.session_id);
            return currentSession;
        }

        // 嘗試從 app 的 WebSocketManager 獲取
        if (window.feedbackApp && window.feedbackApp.webSocketManager) {
            const wsManager = window.feedbackApp.webSocketManager;
            if (wsManager.sessionId) {
                console.log('📋 從 WebSocketManager 獲取數據:', wsManager.sessionId);
                return {
                    session_id: wsManager.sessionId,
                    status: this.getCurrentSessionStatus(),
                    created_at: this.getSessionCreatedTime(),
                    project_directory: this.getProjectDirectory(),
                    summary: this.getAISummary()
                };
            }
        }

        // 嘗試從 app 的 currentSessionId 獲取
        if (window.feedbackApp && window.feedbackApp.currentSessionId) {
            console.log('📋 從 app.currentSessionId 獲取數據:', window.feedbackApp.currentSessionId);
            return {
                session_id: window.feedbackApp.currentSessionId,
                status: this.getCurrentSessionStatus(),
                created_at: this.getSessionCreatedTime(),
                project_directory: this.getProjectDirectory(),
                summary: this.getAISummary()
            };
        }

        console.log('📋 無法獲取會話數據');
        return null;
    };

    /**
     * 獲取會話建立時間
     */
    SessionManager.prototype.getSessionCreatedTime = function() {
        // 嘗試從 WebSocketManager 的連線開始時間獲取
        if (window.feedbackApp && window.feedbackApp.webSocketManager) {
            const wsManager = window.feedbackApp.webSocketManager;
            if (wsManager.connectionStartTime) {
                return wsManager.connectionStartTime / 1000;
            }
        }

        // 嘗試從最後收到的狀態更新中獲取
        if (this.dataManager && this.dataManager.lastStatusUpdate && this.dataManager.lastStatusUpdate.created_at) {
            return this.dataManager.lastStatusUpdate.created_at;
        }

        // 如果都沒有，返回 null
        return null;
    };

    /**
     * 獲取當前會話狀態
     */
    SessionManager.prototype.getCurrentSessionStatus = function() {
        // 嘗試從 UIManager 獲取當前狀態
        if (window.feedbackApp && window.feedbackApp.uiManager) {
            const currentState = window.feedbackApp.uiManager.getFeedbackState();
            if (currentState) {
                // 將內部狀態轉換為會話狀態
                const stateMap = {
                    'waiting_for_feedback': 'waiting',
                    'processing': 'active',
                    'feedback_submitted': 'feedback_submitted'
                };
                return stateMap[currentState] || currentState;
            }
        }

        // 嘗試從最後收到的狀態更新中獲取
        if (this.dataManager && this.dataManager.lastStatusUpdate && this.dataManager.lastStatusUpdate.status) {
            return this.dataManager.lastStatusUpdate.status;
        }

        // 預設狀態
        return 'waiting';
    };

    /**
     * 獲取專案目錄
     */
    SessionManager.prototype.getProjectDirectory = function() {
        const projectElement = document.querySelector('.session-project');
        if (projectElement) {
            return projectElement.textContent.replace('專案: ', '');
        }

        // 從頂部狀態列獲取
        const topProjectInfo = document.querySelector('.project-info');
        if (topProjectInfo) {
            return topProjectInfo.textContent.replace('專案目錄: ', '');
        }

        return '未知';
    };

    /**
     * 獲取 AI 摘要
     */
    SessionManager.prototype.getAISummary = function() {
        const summaryElement = document.querySelector('.session-summary');
        if (summaryElement && summaryElement.textContent !== 'AI 摘要: 載入中...') {
            return summaryElement.textContent.replace('AI 摘要: ', '');
        }

        // 嘗試從主要內容區域獲取
        const mainSummary = document.querySelector('#combinedSummaryContent');
        if (mainSummary && mainSummary.textContent.trim()) {
            return mainSummary.textContent.trim();
        }

        return '暫無摘要';
    };





    /**
     * 更新顯示
     */
    SessionManager.prototype.updateDisplay = function() {
        const currentSession = this.dataManager.getCurrentSession();
        const history = this.dataManager.getSessionHistory();
        const stats = this.dataManager.getStats();

        this.uiRenderer.renderCurrentSession(currentSession);
        this.uiRenderer.renderSessionHistory(history);
        this.uiRenderer.renderStats(stats);
    };

    /**
     * 顯示訊息
     */
    SessionManager.prototype.showMessage = function(message, type) {
        if (window.MCPFeedback && window.MCPFeedback.Utils && window.MCPFeedback.Utils.showMessage) {
            window.MCPFeedback.Utils.showMessage(message, type);
        } else {
            console.log('📋 ' + message);
        }
    };

    /**
     * 匯出會話歷史
     */
    SessionManager.prototype.exportSessionHistory = function() {
        if (!this.dataManager) {
            console.error('📋 DataManager 未初始化');
            return;
        }

        try {
            const filename = this.dataManager.exportSessionHistory();

            // 顯示成功訊息
            if (window.MCPFeedback && window.MCPFeedback.Utils && window.MCPFeedback.Utils.showMessage) {
                const message = window.i18nManager ?
                    window.i18nManager.t('sessionHistory.management.exportSuccess') :
                    '會話歷史已匯出';
                window.MCPFeedback.Utils.showMessage(message + ': ' + filename, 'success');
            }
        } catch (error) {
            console.error('📋 匯出會話歷史失敗:', error);
            if (window.MCPFeedback && window.MCPFeedback.Utils && window.MCPFeedback.Utils.showMessage) {
                const message = window.i18nManager ?
                    window.i18nManager.t('sessionHistory.management.exportFailed', { error: error.message }) :
                    '匯出失敗: ' + error.message;
                window.MCPFeedback.Utils.showMessage(message, 'error');
            }
        }
    };

    /**
     * 匯出單一會話
     */
    SessionManager.prototype.exportSingleSession = function(sessionId) {
        if (!this.dataManager) {
            console.error('📋 DataManager 未初始化');
            return;
        }

        try {
            const filename = this.dataManager.exportSingleSession(sessionId);
            if (filename) {
                // 顯示成功訊息
                if (window.MCPFeedback && window.MCPFeedback.Utils && window.MCPFeedback.Utils.showMessage) {
                    const message = window.i18nManager ?
                        window.i18nManager.t('sessionHistory.management.exportSuccess') :
                        '會話已匯出';
                    window.MCPFeedback.Utils.showMessage(message + ': ' + filename, 'success');
                }
            }
        } catch (error) {
            console.error('📋 匯出單一會話失敗:', error);
            if (window.MCPFeedback && window.MCPFeedback.Utils && window.MCPFeedback.Utils.showMessage) {
                const message = window.i18nManager ?
                    window.i18nManager.t('sessionHistory.management.exportFailed', { error: error.message }) :
                    '匯出失敗: ' + error.message;
                window.MCPFeedback.Utils.showMessage(message, 'error');
            }
        }
    };

    /**
     * 清空會話歷史
     */
    SessionManager.prototype.clearSessionHistory = function() {
        if (!this.dataManager) {
            console.error('📋 DataManager 未初始化');
            return;
        }

        // 確認對話框
        const confirmMessage = window.i18nManager ?
            window.i18nManager.t('sessionHistory.management.confirmClear') :
            '確定要清空所有會話歷史嗎？';

        if (!confirm(confirmMessage)) {
            return;
        }

        try {
            this.dataManager.clearHistory();

            // 顯示成功訊息
            if (window.MCPFeedback && window.MCPFeedback.Utils && window.MCPFeedback.Utils.showMessage) {
                const message = window.i18nManager ?
                    window.i18nManager.t('sessionHistory.management.clearSuccess') :
                    '會話歷史已清空';
                window.MCPFeedback.Utils.showMessage(message, 'success');
            }
        } catch (error) {
            console.error('📋 清空會話歷史失敗:', error);
            if (window.MCPFeedback && window.MCPFeedback.Utils && window.MCPFeedback.Utils.showMessage) {
                const errorMessage = window.i18nManager ?
                    window.i18nManager.t('sessionHistory.management.clearFailed', { error: error.message }) :
                    '清空失敗: ' + error.message;
                window.MCPFeedback.Utils.showMessage(errorMessage, 'error');
            }
        }
    };

    /**
     * 清空用戶訊息記錄
     */
    SessionManager.prototype.clearUserMessages = function() {
        if (!this.dataManager) {
            console.error('📋 DataManager 未初始化');
            return;
        }

        const i18n = window.i18nManager;
        const confirmMessage = i18n ?
            i18n.t('sessionHistory.userMessages.confirmClearAll') :
            '確定要清空所有會話的用戶訊息記錄嗎？此操作無法復原。';

        if (!confirm(confirmMessage)) {
            return;
        }

        try {
            const success = this.dataManager.clearAllUserMessages();
            if (success) {
                const successMessage = i18n ?
                    i18n.t('sessionHistory.userMessages.clearSuccess') :
                    '用戶訊息記錄已清空';
                this.showMessage(successMessage, 'success');
            } else {
                const errorMessage = window.i18nManager ?
                    window.i18nManager.t('sessionHistory.management.clearFailedGeneric', '清空失敗') :
                    '清空失敗';
                this.showMessage(errorMessage, 'error');
            }
        } catch (error) {
            console.error('📋 清空用戶訊息記錄失敗:', error);
            const errorMessage = window.i18nManager ?
                window.i18nManager.t('sessionHistory.management.clearFailed', { error: error.message }) :
                '清空失敗: ' + error.message;
            this.showMessage(errorMessage, 'error');
        }
    };

    /**
     * 清理資源
     */
    SessionManager.prototype.cleanup = function() {
        // 清理子模組
        if (this.dataManager) {
            this.dataManager.cleanup();
            this.dataManager = null;
        }

        if (this.uiRenderer) {
            this.uiRenderer.cleanup();
            this.uiRenderer = null;
        }

        if (this.detailsModal) {
            this.detailsModal.cleanup();
            this.detailsModal = null;
        }



        console.log('📋 SessionManager (重構版) 清理完成');
    };

    // 將 SessionManager 加入命名空間
    window.MCPFeedback.SessionManager = SessionManager;

    // 全域方法供 HTML 調用
    window.MCPFeedback.SessionManager.viewSessionDetails = function(sessionId) {
        console.log('📋 全域查看會話詳情:', sessionId);

        // 找到當前的 SessionManager 實例
        if (window.MCPFeedback && window.MCPFeedback.app && window.MCPFeedback.app.sessionManager) {
            const sessionManager = window.MCPFeedback.app.sessionManager;
            sessionManager.viewSessionDetails(sessionId);
        } else {
            // 如果找不到實例，顯示錯誤訊息
            console.warn('找不到 SessionManager 實例');
            if (window.MCPFeedback && window.MCPFeedback.Utils && window.MCPFeedback.Utils.showMessage) {
                window.MCPFeedback.Utils.showMessage('會話管理器未初始化', 'error');
            }
        }
    };

    /**
     * 复制当前会话内容
     */
    SessionManager.prototype.copyCurrentSessionContent = function() {
        console.log('📋 复制当前会话内容...');

        try {
            const currentSession = this.dataManager.getCurrentSession();
            if (!currentSession) {
                const message = window.i18nManager ?
                    window.i18nManager.t('sessionHistory.currentSession.noData', '沒有當前會話數據') :
                    '沒有當前會話數據';
                this.showMessage(message, 'error');
                return;
            }

            const content = this.formatCurrentSessionContent(currentSession);
            const successMessage = window.i18nManager ?
                window.i18nManager.t('sessionHistory.currentSession.copySuccess', '當前會話內容已複製到剪貼板') :
                '當前會話內容已複製到剪貼板';
            this.copyToClipboard(content, successMessage);
        } catch (error) {
            console.error('复制当前会话内容失败:', error);
            const message = window.i18nManager ?
                window.i18nManager.t('sessionHistory.currentSession.copyFailed', '複製失敗，請重試') :
                '複製失敗，請重試';
            this.showMessage(message, 'error');
        }
    };

    /**
     * 格式化当前会话内容
     */
    SessionManager.prototype.formatCurrentSessionContent = function(sessionData) {
        const lines = [];
        lines.push('# MCP Feedback Enhanced - 当前会话内容');
        lines.push('');
        lines.push(`**会话ID**: ${sessionData.session_id || 'N/A'}`);
        lines.push(`**项目目录**: ${sessionData.project_directory || 'N/A'}`);
        lines.push(`**摘要**: ${sessionData.summary || 'N/A'}`);
        lines.push(`**状态**: ${sessionData.status || 'N/A'}`);
        lines.push(`**创建时间**: ${sessionData.created_at || 'N/A'}`);
        lines.push(`**更新时间**: ${sessionData.updated_at || 'N/A'}`);
        lines.push('');

        if (sessionData.user_messages && sessionData.user_messages.length > 0) {
            lines.push('## 用户消息');
            sessionData.user_messages.forEach((msg, index) => {
                lines.push(`### 消息 ${index + 1}`);
                lines.push(msg);
                lines.push('');
            });
        }

        if (sessionData.ai_responses && sessionData.ai_responses.length > 0) {
            lines.push('## AI 响应');
            sessionData.ai_responses.forEach((response, index) => {
                lines.push(`### 响应 ${index + 1}`);
                lines.push(response);
                lines.push('');
            });
        }

        return lines.join('\n');
    };

    /**
     * 复制到剪贴板
     */
    SessionManager.prototype.copyToClipboard = function(text, successMessage) {
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text).then(() => {
                this.showMessage(successMessage, 'success');
            }).catch(err => {
                console.error('复制到剪贴板失败:', err);
                this.fallbackCopyToClipboard(text, successMessage);
            });
        } else {
            this.fallbackCopyToClipboard(text, successMessage);
        }
    };

    /**
     * 降级复制方法
     */
    SessionManager.prototype.fallbackCopyToClipboard = function(text, successMessage) {
        const textArea = document.createElement('textarea');
        textArea.value = text;
        textArea.style.position = 'fixed';
        textArea.style.left = '-999999px';
        textArea.style.top = '-999999px';
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();

        try {
            document.execCommand('copy');
            this.showMessage(successMessage, 'success');
        } catch (err) {
            console.error('降级复制失败:', err);
            const message = window.i18nManager ?
                window.i18nManager.t('sessionHistory.currentSession.copyFailedManual', '複製失敗，請手動複製') :
                '複製失敗，請手動複製';
            this.showMessage(message, 'error');
        } finally {
            document.body.removeChild(textArea);
        }
    };

    /**
     * 显示消息
     */
    SessionManager.prototype.showMessage = function(message, type) {
        if (window.MCPFeedback && window.MCPFeedback.Utils && window.MCPFeedback.Utils.showMessage) {
            const messageType = type === 'success' ? window.MCPFeedback.Utils.CONSTANTS.MESSAGE_SUCCESS :
                               type === 'warning' ? window.MCPFeedback.Utils.CONSTANTS.MESSAGE_WARNING :
                               window.MCPFeedback.Utils.CONSTANTS.MESSAGE_ERROR;
            window.MCPFeedback.Utils.showMessage(message, messageType);
        } else {
            console.log(`[${type.toUpperCase()}] ${message}`);
        }
    };

    // 全域匯出會話歷史方法
    window.MCPFeedback.SessionManager.exportSessionHistory = function() {
        if (window.MCPFeedback && window.MCPFeedback.app && window.MCPFeedback.app.sessionManager) {
            window.MCPFeedback.app.sessionManager.exportSessionHistory();
        } else {
            console.warn('找不到 SessionManager 實例');
        }
    };

    // 全域匯出單一會話方法
    window.MCPFeedback.SessionManager.exportSingleSession = function(sessionId) {
        if (window.MCPFeedback && window.MCPFeedback.app && window.MCPFeedback.app.sessionManager) {
            window.MCPFeedback.app.sessionManager.exportSingleSession(sessionId);
        } else {
            console.warn('找不到 SessionManager 實例');
        }
    };

    // 全域清空會話歷史方法
    window.MCPFeedback.SessionManager.clearSessionHistory = function() {
        if (window.MCPFeedback && window.MCPFeedback.app && window.MCPFeedback.app.sessionManager) {
            window.MCPFeedback.app.sessionManager.clearSessionHistory();
        } else {
            console.warn('找不到 SessionManager 實例');
        }
    };

    console.log('✅ SessionManager (重構版) 模組載入完成');

})();
