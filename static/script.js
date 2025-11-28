document.addEventListener('DOMContentLoaded', () => {

    // --- Element Selectors ---
    const authContainer = document.getElementById('auth-container');
    const sidebar = document.getElementById('sidebar');
    const chatContainer = document.getElementById('chat-container');

    const userPic = document.getElementById('user-pic');
    const userName = document.getElementById('user-name');
    const chatTitle = document.getElementById('chat-title');

    const sessionList = document.getElementById('session-list');
    const newChatButton = document.getElementById('new-chat-btn');

    const chatMessages = document.getElementById('chat-messages');
    const chatForm = document.getElementById('chat-form');
    const messageInput = document.getElementById('message-input');
    const sendButton = document.getElementById('send-button');

    // --- App State ---
    let currentSessionId = null;
    let isStreaming = false;

    // --- Main Functions ---

    /**
     * Checks authentication status on page load.
     */
    async function checkAuth() {
        try {
            const response = await fetch('/api/me');
            if (response.ok) {
                const user = await response.json();
                showChatUI(user);
                await loadChatSessions();
            } else {
                showAuthUI();
            }
        } catch (error) {
            console.error('Auth check failed:', error);
            showAuthUI();
        }
    }

    /**
     * Shows the chat UI and hides the auth UI.
     */
    function showChatUI(user) {
        userName.textContent = user.name;
        userPic.src = user.picture;

        authContainer.classList.add('hidden');
        sidebar.classList.remove('hidden');
        chatContainer.classList.remove('hidden');
    }

    /**
     * Shows the auth UI and hides the chat UI.
     */
    function showAuthUI() {
        authContainer.classList.remove('hidden');
        sidebar.classList.add('hidden');
        chatContainer.classList.add('hidden');
    }

    /**
     * Loads all chat sessions for the user into the sidebar.
     */
    async function loadChatSessions() {
        try {
            const response = await fetch('/api/sessions');
            if (!response.ok) {
                console.error('Failed to load sessions');
                return;
            }
            const sessions = await response.json();
            sessionList.innerHTML = ''; // Clear existing
            sessions.forEach(session => {
                addSessionToSidebar(session.title, session.id, false);
            });

            // If no session is selected, start a new one
            if (!currentSessionId) {
                startNewChat();
            } else {
                // If there is a session, load the first one by default (or last active)
                // For now, let's just highlight the first one if none is active
                if (sessions.length > 0 && !document.querySelector('.session-item.active')) {
                   await loadSessionMessages(sessions[0].id, sessions[0].title);
                }
            }

        } catch (error) {
            console.error('Error loading sessions:', error);
        }
    }

    /**
     * Loads messages for a specific session and displays them.
     * @param {number} sessionId
     * @param {string} title
     */
    async function loadSessionMessages(sessionId, title) {
        if (isStreaming) return; // Don't switch sessions mid-stream

        try {
            const response = await fetch(`/api/session/${sessionId}/messages`);
            if (!response.ok) {
                console.error('Failed to load messages');
                return;
            }
            const messages = await response.json();

            chatMessages.innerHTML = ''; // Clear chat
            messages.forEach(msg => {
                addMessageToUI(msg.role, msg.content);
            });

            chatTitle.textContent = title;
            currentSessionId = sessionId;
            updateActiveSession(sessionId);

        } catch (error) {
            console.error('Error loading messages:', error);
        }
    }

    /**
     * Handles the chat form submission.
     */
    async function handleChatSubmit(event) {
        event.preventDefault();
        const message = messageInput.value.trim();
        if (!message || isStreaming) return;

        addMessageToUI('user', message);
        messageInput.value = '';
        messageInput.style.height = 'auto'; // Reset height
        setStreamingState(true);

        let aiMessageElement = null; // To store the AI message bubble
        let fullReply = ""; // To store the full reply for history

        try {
            const response = await fetch('/api/chat/stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: message,
                    sessionId: currentSessionId // This will be null for a new chat
                })
            });

            if (!response.ok) {
                throw new Error(`Server error: ${response.statusText}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value);

                // SSE messages can be split, so we process by lines
                const lines = chunk.split('\n');
                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        try {
                            const data = JSON.parse(line.substring(6));

                            if (data.type === 'session_created') {
                                // New session was created by the backend
                                currentSessionId = data.sessionId;
                                chatTitle.textContent = data.title;
                                addSessionToSidebar(data.title, data.sessionId, true); // Add to top
                            }
                            else if (data.type === 'content') {
                                // This is a content chunk
                                if (!aiMessageElement) {
                                    // Create the AI message bubble on first chunk
                                    aiMessageElement = document.createElement('div');
                                    aiMessageElement.classList.add('message', 'ai');
                                    chatMessages.appendChild(aiMessageElement);
                                }
                                // Append new content and handle newlines
                                fullReply += data.chunk;

                                // --- MARKDOWN FIX: Parse HTML ---
                                if (window.marked) {
                                    aiMessageElement.innerHTML = window.marked.parse(fullReply);
                                } else {
                                    aiMessageElement.innerHTML = fullReply.replace(/\n/g, '<br>');
                                }
                                // --- End of MARKDOWN FIX ---

                                scrollToBottom();
                            }
                            else if (data.type === 'error') {
                                throw new Error(data.message);
                            }

                        } catch (e) {
                            console.error('Error parsing stream data:', e, 'Line:', line);
                        }
                    }
                }
            }
        } catch (error) {
            console.error('Error sending message:', error);
            if (aiMessageElement) {
                aiMessageElement.innerHTML += `<br><br><strong style="color: red;">Error: ${error.message}</strong>`;
            } else {
                addMessageToUI('ai', `Sorry, an error occurred: ${error.message}`);
            }
        } finally {
            setStreamingState(false);
        }
    }

    // --- UI Helper Functions ---

    /**
     * Creates a new message element and returns it.
     * @param {string} role - 'user' or 'ai'
     * @param {string} content - The message text
     * @returns {HTMLElement} The created message element
     */
    function createMessageElement(role, content) {
        const messageElement = document.createElement('div');
        messageElement.classList.add('message', role);

        // --- MARKDOWN FIX: Parse AI messages ---
        if (role === 'ai' && window.marked) {
            messageElement.innerHTML = window.marked.parse(content);
        } else {
            // User messages or if marked fails
            messageElement.innerHTML = content.replace(/\n/g, '<br>');
        }
        // --- End of MARKDOWN FIX ---

        return messageElement;
    }

    /**
     * Adds a message to the chat UI.
     * @param {string} role - 'user' or 'ai'
     * @param {string} content - The message text
     */
    function addMessageToUI(role, content) {
        // createMessageElement now handles markdown parsing
        const messageElement = createMessageElement(role, content);
        chatMessages.appendChild(messageElement);
        scrollToBottom();
    }

    /**
     * Adds a session to the sidebar list.
     * @param {string} title
     * @param {number} sessionId
     * @param {boolean} [prepend=false] - Whether to add to the top
     */
    function addSessionToSidebar(title, sessionId, prepend = false) {
        // Avoid adding a session if it's already there
        if (document.querySelector(`.session-item[data-session-id="${sessionId}"]`)) {
            updateActiveSession(sessionId); // Just highlight it
            return;
        }

        const sessionItem = document.createElement('div');
        sessionItem.classList.add('session-item');
        sessionItem.textContent = title;
        sessionItem.dataset.sessionId = sessionId;
        sessionItem.dataset.title = title;

        sessionItem.addEventListener('click', () => {
            loadSessionMessages(sessionId, title);
        });

        if (prepend) {
            sessionList.prepend(sessionItem);
        } else {
            sessionList.appendChild(sessionItem);
        }

        updateActiveSession(currentSessionId);
    }

    /**
     * Highlights the active session in the sidebar.
     * @param {number} sessionId
     */
    function updateActiveSession(sessionId) {
        document.querySelectorAll('.session-item').forEach(item => {
            if (item.dataset.sessionId == sessionId) {
                item.classList.add('active');
            } else {
                item.classList.remove('active');
            }
        });
    }

    /**
     * Clears the chat and starts a new session.
     */
    function startNewChat() {
        if (isStreaming) return;
        currentSessionId = null;
        chatMessages.innerHTML = '';
        chatTitle.textContent = 'New Chat';
        updateActiveSession(null);
    }

    /**
     * Scrolls the chat-messages div to the bottom.
     */
    function scrollToBottom() {
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    /**
     * Toggles the UI state for streaming.
     * @param {boolean} streaming
     */
    function setStreamingState(streaming) {
        isStreaming = streaming;
        sendButton.disabled = streaming;
        messageInput.disabled = streaming;
        if (streaming) {
            sendButton.textContent = '...';
        } else {
            sendButton.textContent = 'Send';
        }
    }

    // --- Event Listeners ---

    // Auto-resize textarea
    messageInput.addEventListener('input', () => {
        messageInput.style.height = 'auto';
        messageInput.style.height = (messageInput.scrollHeight) + 'px';
    });

    // Submit on Enter (but not Shift+Enter)
    messageInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            chatForm.dispatchEvent(new Event('submit'));
        }
    });

    // Handle form submit
    chatForm.addEventListener('submit', handleChatSubmit);

    // New chat button
    newChatButton.addEventListener('click', startNewChat);

    // Initial check
    checkAuth();
});