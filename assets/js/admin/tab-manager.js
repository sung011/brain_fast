document.addEventListener('DOMContentLoaded', function () {
    // 1. 필요한 HTML 요소들을 가져옵니다.
    // 이 변수들은 스크립트 전체에서 재사용됩니다.
    const sidebarNav = document.getElementById('layoutSidenav_nav');
    const tabContainer = document.getElementById('myTab');
    const tabContentContainer = document.getElementById('myTabContent');
    const mainContent = document.getElementById('main-content');

    // 2. 필수 요소들이 페이지에 존재하는지 확인하여 오류를 방지합니다.
    if (!sidebarNav || !tabContainer || !tabContentContainer || !mainContent) {
        console.error('필수 HTML 요소(ID)가 페이지에 존재하지 않습니다: layoutSidenav_nav, myTab, myTabContent, main-content');
        return;
    }

    // 3. 탭의 유무에 따라 메인 콘텐츠와 탭 컨테이너의 표시 상태를 업데이트하는 함수입니다.
    const updateTabUI = () => {
        if (tabContainer.childElementCount === 0) {
            mainContent.style.display = 'block';
            tabContainer.style.display = 'none';
            tabContentContainer.style.display = 'none';
        } else {
            mainContent.style.display = 'none';
            tabContainer.style.display = 'flex';
            tabContentContainer.style.display = 'block';
        }
    };

    // 4. 탭을 생성하고 관리하는 핵심 함수입니다.
    const createOrShowTab = (linkElement) => {
        const url = linkElement.getAttribute('href');
        const title = linkElement.dataset.tabTitle || linkElement.textContent.trim();
        // [추가] 링크에 data-usertype 속성이 있는지 확인하여 링크 유형을 구분합니다.
        const hasUserType = linkElement.hasAttribute('data-usertype');

        // [수정] 링크 유형에 따라 고유 ID와 불러올 URL을 동적으로 결정합니다.
        let tabContentId, tabButtonId, fetchUrl;
        let tableId = null; // DataTables용 ID는 userType이 있을 때만 사용
        let ajaxUrl = null; // ajax URL도 userType이 있을 때만 사용
        let userType = null; // [추가] userType을 저장할 변수

        if (hasUserType) {
            // --- data-usertype이 있는 링크 (회원 목록 등 템플릿 사용) 처리 ---
            userType = linkElement.dataset.usertype;
            tabContentId = `tab-content-${userType}`;
            tabButtonId = `tab-button-${userType}`;
            tableId = `datatable-${userType}`;
            ajaxUrl = `/admin/api/members/${userType}`;
            fetchUrl = '/admin/user?partial=1'; // 회원 목록은 레이아웃 없는 본문만 가져온다
        } else {
            // --- data-usertype이 없는 일반 링크 (Dashboard 등) 처리 ---
            const uniqueId = url.replace(/[^a-zA-Z0-9]/g, '');
            tabContentId = `tab-content-${uniqueId}`;
            tabButtonId = `tab-button-${uniqueId}`;
            fetchUrl = url; // 해당 링크의 href를 직접 fetch
        }

        // 이미 탭이 존재하는지 확인하고, 있다면 활성화 후 함수를 종료합니다.
        const existingTabButton = document.getElementById(tabButtonId);
        if (existingTabButton) {
            new bootstrap.Tab(existingTabButton).show();
            return;
        }

        // 새 탭 버튼과 콘텐츠 영역을 생성합니다.
        const newTabButton = document.createElement('button');
        newTabButton.id = tabButtonId;
        newTabButton.className = 'nav-link';
        newTabButton.setAttribute('data-bs-toggle', 'tab');
        newTabButton.setAttribute('data-bs-target', `#${tabContentId}`);
        newTabButton.innerHTML = `${title} <button type="button" class="btn-close ms-2" aria-label="Close"></button>`;

        const newTabItem = document.createElement('li');
        newTabItem.appendChild(newTabButton);
        tabContainer.appendChild(newTabItem);

        const newTabContent = document.createElement('div');
        newTabContent.id = tabContentId;
        newTabContent.className = 'tab-pane fade';
        tabContentContainer.appendChild(newTabContent);

        // [추가] 탭이 화면에 표시된 후, DataTables 컬럼을 재조정하는 이벤트 리스너를 추가합니다.
        // 이것은 숨겨진 탭에서 테이블이 깨지는 현상을 해결합니다.
        newTabButton.addEventListener('shown.bs.tab', function () {
            if (tableId) { // tableId가 있는 경우 (회원 목록 탭)에만 실행
                const table = document.getElementById(tableId);
                if (table && $.fn.DataTable.isDataTable(table)) {
                    $(table).DataTable().columns.adjust().draw();
                }
            }
        });

        newTabContent.innerHTML = `<div class="card"><div class="card-body"><h5>${title} 페이지 로딩 중...</h5></div></div>`;

        fetch(fetchUrl)
            .then(response => {
                if (!response.ok) throw new Error('Content file not found');
                return response.text();
            })
            .then(html => {
                const parser = new DOMParser();
                const doc = parser.parseFromString(html, 'text/html');

                // [✅ 핵심 수정] userType이 있는 경우, '회원가입' 버튼의 링크와 텍스트를 동적으로 변경합니다.
                if (hasUserType) {
                    const addUserButton = doc.querySelector('.add-user-button');
                    if (addUserButton) {
                        addUserButton.setAttribute('href', '/admin/user/signup?partial=1');
                        addUserButton.setAttribute('data-tab-title', '회원가입');

                        const buttonTextElement = addUserButton.querySelector('.button-text');
                        if (buttonTextElement) {
                            if (userType === 'seller') {
                                buttonTextElement.textContent = '판매자 가입';
                            } else {
                                buttonTextElement.textContent = '회원가입';
                            }
                        }
                    }
                }

                // 전체 레이아웃 HTML이 오면 #main-content만 쓰고, 본문 조각이면 body 전체를 넣는다.
                const main = doc.querySelector('#main-content');
                newTabContent.innerHTML = main ? main.innerHTML : doc.body.innerHTML;

                // [수정] 스크립트를 순차적으로 실행하는 로직으로 대폭 개선되었습니다.
                // 이 부분은 Chart.js나 DataTables 스크립트가 정상 작동하도록 보장합니다.
                const scripts = Array.from(newTabContent.querySelectorAll('script'));
                scripts.forEach(s => s.remove()); // 원래 있던 실행 안 되는 스크립트 태그들은 미리 제거

                // async 즉시 실행 함수로 스크립트를 하나씩 순서대로 실행
                (async () => {
                    for (const script of scripts) {
                        const newScript = document.createElement('script');
                        let scriptContent = script.innerHTML;

                        // [추가] userType이 있는 경우 플레이схол더를 실제 값으로 교체합니다.
                        if (hasUserType) {
                            const tableElement = newTabContent.querySelector('.datatable-target');
                            if (tableElement) tableElement.id = tableId;

                            scriptContent = scriptContent.replace(/__TABLE_ID__/g, `#${tableId}`);
                            scriptContent = scriptContent.replace(/__AJAX_URL__/g, ajaxUrl);
                        }

                        newScript.innerHTML = scriptContent;

                        // [추가] 외부 스크립트(.src)인 경우, 로드가 완료될 때까지 기다립니다.
                        if (script.src) {
                            newScript.src = script.src;
                            await new Promise((resolve, reject) => {
                                newScript.onload = resolve;
                                newScript.onerror = reject;
                                document.body.appendChild(newScript);
                            });
                        } else {
                            // 인라인 스크립트는 추가 즉시 실행됨
                            document.body.appendChild(newScript);
                        }
                        // 실행 후 임시로 추가했던 스크립트 태그 정리
                        document.body.removeChild(newScript);
                    }
                })();
            })
            .catch(error => {
                newTabContent.innerHTML = `<p class="p-3">콘텐츠를 불러오는 데 실패했습니다.</p>`;
                console.error('Error fetching tab content:', error);
            });

        updateTabUI();
        new bootstrap.Tab(newTabButton).show();
    };

    // 5. 사이드바 링크에 클릭 이벤트 리스너를 설정합니다. (이벤트 위임)
    sidebarNav.addEventListener('click', function (event) {
        const link = event.target.closest('a.nav-link');

        // 링크가 아니거나 아코디언 토글 버튼이면 무시합니다.
        if (!link || link.dataset.bsToggle === 'collapse') {
            return;
        }

        const href = link.getAttribute('href');
        // [수정] 유효하지 않은 href 속성을 가진 링크는 탭으로 만들지 않도록 조건을 강화했습니다.
        if (!href || href === '#!' || href.startsWith('javascript:')) {
            return;
        }
        // 화면 공유 분석은 전체 페이지로 연다 (탭 안에서는 getDisplayMedia UX가 깨짐)
        if (link.dataset.fullPage === '1') {
            return;
        }

        // [수정] 모든 유효한 링크의 기본 동작(페이지 이동)을 막고 탭 생성 함수를 호출하도록 변경했습니다.
        event.preventDefault();
        createOrShowTab(link);
    });

    // 6. 탭 컨테이너에 닫기 버튼 이벤트 리스너를 설정합니다. (이벤트 위임)
    tabContainer.addEventListener('click', function (event) {
        const closeButton = event.target.closest('.btn-close');
        if (!closeButton) return;

        const tabButtonToRemove = closeButton.parentElement;
        const tabItemToRemove = tabButtonToRemove.parentElement;
        const contentId = tabButtonToRemove.getAttribute('data-bs-target');
        const contentToRemove = document.querySelector(contentId);

        const wasActive = tabButtonToRemove.classList.contains('active');
        tabItemToRemove.remove();
        if (contentToRemove) contentToRemove.remove();

        if (wasActive && tabContainer.lastChild) {
            const lastTabButton = tabContainer.lastChild.querySelector('.nav-link');
            if (lastTabButton) new bootstrap.Tab(lastTabButton).show();
        }
        updateTabUI();
    });

    // 7. 페이지 로드 시 초기 UI 상태를 설정합니다.
    updateTabUI();

    // 8. [✅ 새로 추가된 부분] 탭 콘텐츠 내부에서 발생하는 클릭을 처리하는 이벤트 리스너
    tabContentContainer.addEventListener('click', function (event) {
        // 클릭된 요소가 탭 생성을 유발하는 링크(<a> 태그)인지 확인합니다.
        //const link = event.target.closest('a');
        const link = event.target.closest('a.open-in-tab');

        // 링크가 아니거나, 유효하지 않은 href를 가졌다면 무시합니다.
        if (!link) return;
        const href = link.getAttribute('href');
        if (!href || href === '#!' || href.startsWith('javascript:')) {
            return;
        }

        // 유효한 링크라면, 기본 동작을 막고 탭 생성 함수를 호출합니다.
        event.preventDefault();
        createOrShowTab(link);
    });
});

