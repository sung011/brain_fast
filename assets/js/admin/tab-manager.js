/**
 * 관리자 탭 매니저
 * - 사이드바/탭 내부 open-in-tab 링크로 탭 생성
 * - partial URL 정규화로 동일 페이지 탭 중복 방지
 * - 탭 콘텐츠 스코프로 페이지 init 호출 (중복 id 문제 방지)
 */
document.addEventListener('DOMContentLoaded', function () {
    const sidebarNav = document.getElementById('layoutSidenav_nav');
    const tabContainer = document.getElementById('myTab');
    const tabContentContainer = document.getElementById('myTabContent');
    const mainContent = document.getElementById('main-content');

    if (!sidebarNav || !tabContainer || !tabContentContainer || !mainContent) {
        console.error('필수 HTML 요소(ID)가 페이지에 존재하지 않습니다: layoutSidenav_nav, myTab, myTabContent, main-content');
        return;
    }

    function normalizeUrl(url) {
        try {
            const u = new URL(url, window.location.origin);
            if (!u.searchParams.has('partial')) {
                u.searchParams.set('partial', '1');
            }
            return u.pathname + u.search;
        } catch (e) {
            if (!url) return url;
            if (url.indexOf('partial=') >= 0) return url;
            return url.indexOf('?') >= 0 ? (url + '&partial=1') : (url + '?partial=1');
        }
    }

    function tabKeyFromUrl(url) {
        try {
            const u = new URL(url, window.location.origin);
            return u.pathname.replace(/[^a-zA-Z0-9]/g, '') || 'tab';
        } catch (e) {
            return String(url || 'tab').replace(/[^a-zA-Z0-9]/g, '') || 'tab';
        }
    }

    function updateTabUI() {
        if (tabContainer.childElementCount === 0) {
            mainContent.style.display = 'block';
            tabContainer.style.display = 'none';
            tabContentContainer.style.display = 'none';
        } else {
            mainContent.style.display = 'none';
            tabContainer.style.display = 'flex';
            tabContentContainer.style.display = 'block';
        }
    }

    function runPageInits(tabPane) {
        if (!tabPane) return;
        if (tabPane.querySelector('#studyTable') && typeof window.initStudyManager === 'function') {
            window.initStudyManager(tabPane);
        }
        if (tabPane.querySelector('#studyDetailRoot') && typeof window.initStudyDetail === 'function') {
            window.initStudyDetail(tabPane);
        }
        if (tabPane.querySelector('#studyCreateForm') && typeof window.initStudyCreate === 'function') {
            window.initStudyCreate(tabPane);
        }
        if (tabPane.querySelector('#reviewsTable') && typeof window.initReviewsManager === 'function') {
            window.initReviewsManager(tabPane);
        }
        if (tabPane.querySelector('#popupTable') && typeof window.initPopupManager === 'function') {
            window.initPopupManager(tabPane);
        }
        if (tabPane.querySelector('#popupCreateForm') && typeof window.initPopupCreate === 'function') {
            window.initPopupCreate(tabPane);
        }
        if (tabPane.querySelector('#popupDetailRoot') && typeof window.initPopupDetail === 'function') {
            window.initPopupDetail(tabPane);
        }
        if (tabPane.querySelector('#qaPageRoot') && typeof window.initQaManager === 'function') {
            window.initQaManager(tabPane);
        }
        if (tabPane.querySelector('#qaDetailRoot') && typeof window.initQaDetail === 'function') {
            window.initQaDetail(tabPane);
        }
        if (typeof window.feather !== 'undefined') {
            window.feather.replace();
        }
    }

    async function executeScripts(scripts, tabPane, hasUserType, tableId, ajaxUrl) {
        for (const script of scripts) {
            const newScript = document.createElement('script');
            let scriptContent = script.innerHTML;

            if (hasUserType) {
                const tableElement = tabPane.querySelector('.datatable-target');
                if (tableElement && tableId) tableElement.id = tableId;
                scriptContent = scriptContent.replace(/__TABLE_ID__/g, `#${tableId}`);
                scriptContent = scriptContent.replace(/__AJAX_URL__/g, ajaxUrl);
            }

            if (script.src) {
                const bust = (script.src.indexOf('?') >= 0 ? '&' : '?') + '_tab=' + Date.now();
                newScript.src = script.src + bust;
                await new Promise(function (resolve, reject) {
                    newScript.onload = resolve;
                    newScript.onerror = function () {
                        console.error('Tab script load failed:', newScript.src);
                        resolve();
                    };
                    document.body.appendChild(newScript);
                });
            } else {
                newScript.text = scriptContent;
                document.body.appendChild(newScript);
            }

            if (newScript.parentNode) {
                document.body.removeChild(newScript);
            }
        }
        runPageInits(tabPane);
    }

    const createOrShowTab = (linkElement) => {
        const rawUrl = linkElement.getAttribute('data-tab-href')
            || linkElement.getAttribute('href');
        if (!rawUrl || rawUrl === '#!' || rawUrl.startsWith('javascript:')) {
            return;
        }
        const title = linkElement.dataset.tabTitle || linkElement.textContent.trim();
        const hasUserType = linkElement.hasAttribute('data-usertype');

        let tabContentId;
        let tabButtonId;
        let fetchUrl;
        let tableId = null;
        let ajaxUrl = null;
        let userType = null;

        if (hasUserType) {
            userType = linkElement.dataset.usertype;
            tabContentId = `tab-content-${userType}`;
            tabButtonId = `tab-button-${userType}`;
            tableId = `datatable-${userType}`;
            ajaxUrl = `/admin/api/members/${userType}`;
            fetchUrl = '/admin/user?partial=1';
        } else {
            const key = tabKeyFromUrl(rawUrl);
            tabContentId = `tab-content-${key}`;
            tabButtonId = `tab-button-${key}`;
            fetchUrl = normalizeUrl(rawUrl);
        }

        const existingTabButton = document.getElementById(tabButtonId);
        if (existingTabButton) {
            new bootstrap.Tab(existingTabButton).show();
            updateTabUI();
            const target = existingTabButton.getAttribute('data-bs-target');
            const pane = target ? document.querySelector(target) : null;
            if (pane && pane.querySelector('#qaPageRoot') && window.__pendingQaIdx) {
                const idx = window.__pendingQaIdx;
                window.__pendingQaIdx = null;
                document.dispatchEvent(new CustomEvent('admin:qa:open', {detail: {idx: idx}}));
            }
            return;
        }

        const newTabButton = document.createElement('button');
        newTabButton.type = 'button';
        newTabButton.id = tabButtonId;
        newTabButton.className = 'nav-link d-inline-flex align-items-center';
        newTabButton.setAttribute('data-bs-toggle', 'tab');
        newTabButton.setAttribute('data-bs-target', `#${tabContentId}`);
        newTabButton.setAttribute('role', 'tab');
        newTabButton.textContent = title;

        // button 안에 button을 넣지 않음 (중첩 시 클릭/탭 전환이 깨질 수 있음)
        const closeBtn = document.createElement('span');
        closeBtn.className = 'btn-close ms-2';
        closeBtn.setAttribute('role', 'button');
        closeBtn.setAttribute('aria-label', 'Close');
        closeBtn.style.fontSize = '0.55rem';
        newTabButton.appendChild(closeBtn);

        const newTabItem = document.createElement('li');
        newTabItem.className = 'nav-item';
        newTabItem.setAttribute('role', 'presentation');
        newTabItem.appendChild(newTabButton);
        tabContainer.appendChild(newTabItem);

        const newTabContent = document.createElement('div');
        newTabContent.id = tabContentId;
        newTabContent.className = 'tab-pane fade';
        newTabContent.setAttribute('role', 'tabpanel');
        tabContentContainer.appendChild(newTabContent);

        newTabButton.addEventListener('shown.bs.tab', function () {
            if (tableId) {
                const table = document.getElementById(tableId);
                if (table && window.jQuery && window.jQuery.fn.DataTable.isDataTable(table)) {
                    window.jQuery(table).DataTable().columns.adjust().draw();
                }
            }
        });

        newTabContent.innerHTML = `<div class="card"><div class="card-body"><h5>${title} 페이지 로딩 중...</h5></div></div>`;

        fetch(fetchUrl)
            .then(function (response) {
                if (!response.ok) throw new Error('Content file not found');
                return response.text();
            })
            .then(function (html) {
                const parser = new DOMParser();
                const doc = parser.parseFromString(html, 'text/html');

                if (hasUserType) {
                    const addUserButton = doc.querySelector('.add-user-button');
                    if (addUserButton) {
                        addUserButton.setAttribute('href', '/admin/user/signup?partial=1');
                        addUserButton.setAttribute('data-tab-title', '회원가입');
                        const buttonTextElement = addUserButton.querySelector('.button-text');
                        if (buttonTextElement) {
                            buttonTextElement.textContent = userType === 'seller' ? '판매자 가입' : '회원가입';
                        }
                    }
                }

                const main = doc.querySelector('#main-content');
                newTabContent.innerHTML = main ? main.innerHTML : doc.body.innerHTML;

                const scripts = Array.from(newTabContent.querySelectorAll('script'));
                scripts.forEach(function (s) { s.remove(); });

                executeScripts(scripts, newTabContent, hasUserType, tableId, ajaxUrl).catch(function (err) {
                    console.error('Tab script execution failed:', err);
                    runPageInits(newTabContent);
                });
            })
            .catch(function (error) {
                newTabContent.innerHTML = '<p class="p-3">콘텐츠를 불러오는 데 실패했습니다.</p>';
                console.error('Error fetching tab content:', error);
            });

        updateTabUI();
        new bootstrap.Tab(newTabButton).show();
    };

    sidebarNav.addEventListener('click', function (event) {
        const link = event.target.closest('a.nav-link');
        if (!link || link.dataset.bsToggle === 'collapse') return;

        const href = link.getAttribute('href');
        if (!href || href === '#!' || href.startsWith('javascript:')) return;
        if (link.dataset.fullPage === '1') return;

        event.preventDefault();
        createOrShowTab(link);
    });

    tabContainer.addEventListener('click', function (event) {
        const closeButton = event.target.closest('.btn-close');
        if (!closeButton) return;

        event.preventDefault();
        event.stopPropagation();

        const tabButtonToRemove = closeButton.closest('button.nav-link') || closeButton.parentElement;
        const tabItemToRemove = tabButtonToRemove && tabButtonToRemove.closest('li');
        const contentId = tabButtonToRemove && tabButtonToRemove.getAttribute('data-bs-target');
        const contentToRemove = contentId
            ? document.getElementById(contentId.replace(/^#/, ''))
            : null;

        const wasActive = tabButtonToRemove && tabButtonToRemove.classList.contains('active');
        if (tabItemToRemove) tabItemToRemove.remove();
        if (contentToRemove) contentToRemove.remove();

        if (wasActive && tabContainer.lastChild) {
            const lastTabButton = tabContainer.lastChild.querySelector('.nav-link');
            if (lastTabButton) new bootstrap.Tab(lastTabButton).show();
        }
        updateTabUI();
    });

    updateTabUI();

    tabContentContainer.addEventListener('click', function (event) {
        const link = event.target.closest('a.open-in-tab');
        if (!link) return;
        const href = link.getAttribute('href');
        if (!href || href === '#!' || href.startsWith('javascript:')) return;

        event.preventDefault();
        createOrShowTab(link);
    });

    // 상단 Message Center 등 사이드바/탭 밖 open-in-tab
    document.addEventListener('click', function (event) {
        const link = event.target.closest('a.open-in-tab');
        if (!link) return;
        if (sidebarNav.contains(link) || tabContentContainer.contains(link)) return;
        const href = link.getAttribute('data-tab-href') || link.getAttribute('href');
        if (!href || href === '#!' || href.startsWith('javascript:')) return;

        event.preventDefault();
        event.stopPropagation();
        createOrShowTab(link);
    });

    window.AdminTabs = {
        open: createOrShowTab,
        openUrl: function (url, title, extra) {
            const a = document.createElement('a');
            a.setAttribute('href', url);
            a.setAttribute('data-tab-href', url);
            if (title) a.setAttribute('data-tab-title', title);
            if (extra && extra.qaIdx != null) {
                window.__pendingQaIdx = String(extra.qaIdx);
            }
            createOrShowTab(a);
        },
    };
});
