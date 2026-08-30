/**
 * 공통 유틸리티 모듈
 */
const utils = (function () {
    return {
        // swal 기본 스타일
        showAlert(message, callback, timer) {
            if (timer == undefined) timer = 0;

            swal.fire({
                html: message,
                confirmButtonText: '확인',
                timer: (timer > 0) ? timer : 0,
                timerProgressBar: (timer > 0) ? true : false,
                didDestroy: () => {
                    if (callback) callback();
                }
            });
        },

        // confirm
        showConfirm(message) {
            return Swal.fire({
                html: message,
                confirmButtonText: '확인',
                denyButtonText: '취소',
                showDenyButton: true
            });
        },

        showPayConfirm(message) {
            return Swal.fire({
                html: message,
                confirmButtonText: '결제',
                denyButtonText: '취소',
                showDenyButton: true
            });
        },

        // toast 팝업
        showToast(message, callback = null, duration = 1200) {
            const Toast = Swal.mixin({
                toast: true,
                // position: 'top-center',
                showConfirmButton: false,
                timer: duration,
                timerProgressBar: true,
            });

            Toast.fire({
                //icon: 'success',
                title: message,
                didDestroy: () => {
                    if (callback) callback();
                }
            });
        },

        // 로그인
        async showLoginConfirm(message) {
            if (message == undefined) message = '로그인이 필요해요.<br>로그인으로 이동하시겠어요?';
            const confirmResult = await this.showConfirm(message);
            if (confirmResult.isConfirmed === true) {
                location.href = `/login`;
            } else {
                return false;
            }
        },

        // 공통 error
        showError(html, isRefresh, destroyEvent) {
            Swal.fire({
                icon: 'error',
                title: '요청 작업 실패',
                html: html ? html : '서버에 일시적인 오류가 발생했어요.<br>잠시 후 다시 시도해 주세요.',
                confirmButtonText: '확인',
                // showCancelButton: true,
                // cancelButtonText: '닫기',
                timer: 3000,
                timerProgressBar: true,
                didDestroy: () => {
                    if (destroyEvent) destroyEvent();
                    else if (isRefresh) location.reload();
                },
            });
        },

        // 생년월일 하이픈 생성
        addHyphenBirth(value) {
            const birth = value.replace(/[^0-9]/g, "");
            let formatted = '';
            if (birth.length > 4) {
                formatted += birth.substr(0, 4);
                if (birth.length < 6) {
                    formatted += '-' + birth.substr(4, 2);
                } else {
                    formatted += '-' + birth.substr(4, 2);
                    formatted += '-' + birth.substr(6);
                }
            } else {
                formatted += birth;
            }
            return formatted.slice(0, 10);
        },

        // 휴대폰번호, 대표전화 하이픈 생성
        addHyphenTel(value) {
            if (!value) return '';
            let formatted = value.replace(/[^0-9]/g, "");
            if (formatted.length > 11) {
                return value.slice(0, 13);
            }

            let result = [];
            let restNumber = "";

            // 지역번호와 나머지 번호로 나누기
            if (formatted.startsWith("02")) {
                // 서울 02 지역번호
                result.push(formatted.substr(0, 2));
                restNumber = formatted.substring(2);
            } else if (formatted.startsWith("1")) {
                // 지역 번호가 없는 경우
                // 1xxx-yyyy
                restNumber = formatted;
            } else {
                // 나머지 3자리 지역번호
                // 0xx-yyyy-zzzz
                result.push(formatted.substr(0, 3));
                restNumber = formatted.substring(3);
            }

            if (restNumber.length === 7) {
                // 7자리만 남았을 때는 xxx-yyyy
                result.push(restNumber.substring(0, 3));
                result.push(restNumber.substring(3));
            } else {
                result.push(restNumber.substring(0, 4));
                result.push(restNumber.substring(4));
            }

            return result.filter((val) => val).join("-");
        },

        addHyphenTel050(value) {
            if (!value) return '';
            let formatted = value.replace(/[^0-9]/g, "");
            if (formatted.length > 11) {
                return value.slice(0, 14);
            }

            let result = [];
            let restNumber = "";

            // 지역번호와 나머지 번호로 나누기
            if (formatted.startsWith("02")) {
                // 서울 02 지역번호
                result.push(formatted.substr(0, 2));
                restNumber = formatted.substring(2);
            } else if (formatted.startsWith("1")) {
                // 지역 번호가 없는 경우
                // 1xxx-yyyy
                restNumber = formatted;
            } else {
                // 나머지 3자리 지역번호
                // 0xx-yyyy-zzzz
                result.push(formatted.substr(0, 4));
                restNumber = formatted.substring(4);
            }

            if (restNumber.length === 7) {
                // 7자리만 남았을 때는 xxx-yyyy
                result.push(restNumber.substring(0, 4));
                result.push(restNumber.substring(4));
            } else {
                result.push(restNumber.substring(0, 4));
                result.push(restNumber.substring(4));
            }

            return result.filter((val) => val).join("-");
        },

        // 사업자 번호
        businessNumber(value) {
            if (!value) return '';
            let formatted = value.replace(/[^0-9]/g, "")
            if (formatted.length > 9) {
                return value.slice(0, 12);
            }

            // 길이에 따른 하이픈 추가
            if (formatted.length <= 3) {
                return formatted; // 3자리 이하일 경우 그대로 반환
            } else if (formatted.length <= 5) {
                // 4~5자리일 경우 "xxx-xx" 형식으로 변환
                return `${formatted.slice(0, 3)}-${formatted.slice(3)}`;
            } else {
                // 6자리 이상일 경우 "xxx-xx-xxxxx" 형식으로 변환
                return `${formatted.slice(0, 3)}-${formatted.slice(3, 5)}-${formatted.slice(5)}`;
            }

        },

        // 문자열 공백제거
        removeWhitespace(str) {
            return str ? str.replace(/\s+/g, '') : '';
        },

        // 숫자 천단위 콤마
        addCommaNumber(value, allowNegative = false) {
            const number = this.toNumber(value, allowNegative);

            if (allowNegative) {
                if (number == '') return '';
                else number.replace(/(\d)(?=(\d{3})+(?!\d))/g, '$1,');
            }

            return number == "0" ? "0" : number.toString().replace(/(\d)(?=(\d{3})+(?!\d))/g, '$1,');
        },

        // 숫자 콤마 제거
        removeCommaNumber(value) {
            return Number(value.replace(/,/gi, ''));
        },

        // 숫자형 반환 (allowNegative: 음수 허용, returnAsString: 문자열 반환)
        toNumber(value, allowNegative = false, returnAsString = false) {
            if (!value) value = '0';
            let minusSign = '';
            if (allowNegative && value.startsWith('-')) minusSign = '-';

            let numberPattern = "\\d"; // 기본: 숫자만 허용
            const regex = new RegExp(`[^${numberPattern}]`, "g");
            const numberStr = value.toString().replace(regex, "");

            const finalStr = (numberStr === '0') ? numberStr : minusSign + numberStr;

            if (returnAsString) {
                return finalStr;
            } else {
                const num = parseInt(finalStr, 10);
                return isNaN(num) ? 0 : num;
            }
        },

        // 소수점 반환
        toDecimal(value, isComma = false, decimalPlaces = 2) {
            value = value.toString().replace(/[^0-9.]/g, '').replace(/(\..*)\./g, '$1');

            // 소수점 이하 n번째 자리수
            let regExp = new RegExp(`^\\d*\\.?\\d{0,${decimalPlaces}}$`);
            if (!regExp.test(value)) {
                value = isNaN(value) ? '0' : Number(value).toFixed(decimalPlaces);
            }

            if (isComma) {
                value = value.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
            }

            return value;
        },

        // 숫자만 추출
        extractNumbers(str) {
            return str.replace(/\D/g, '');
        },

        // 체크박스 전체선택
        selectAllCheckbox(element, name) {
            const checkboxes = document.querySelectorAll(`input[name="${name}"]`);
            checkboxes.forEach((checkbox) => {
                if (!checkbox.disabled)
                    checkbox.checked = element.checked;
            });
        },

        // 체크박스 선택값 리턴
        // selectCheckBoxValues(name) {
        //     let arr = [];
        //     const checkboxes = document.querySelectorAll(`input[name="${name}"]:checked`);
        //     checkboxes.forEach((checkbox) => {
        //         arr.push(checkbox.value);
        //     });
        //     return arr;
        // },

        // Date to YYYY-mm-dd 포맷변환
        formatDate(date) {
            let year = date.getFullYear();
            let month = ('0' + (date.getMonth() + 1)).slice(-2);

            let day = ('0' + date.getDate()).slice(-2);
            return `${year}-${month}-${day}`;
        },

        // 날짜기간 생성
        getStartAndEndDate(rangeType) {
            let returnDateRange = {start: '', end: ''};
            const today = new Date();

            switch (rangeType.toString()) {
                case "1" : // 오늘
                case "now" :
                    returnDateRange.start = this.formatDate(today);
                    returnDateRange.end = this.formatDate(today);
                    break;

                case "2" : // 이번주
                case "week" :
                    let firstDay = new Date(today);
                    firstDay.setDate(today.getDate() - today.getDay() + (today.getDay() === 0 ? -6 : 1));
                    let lastDay = new Date(firstDay);
                    lastDay.setDate(firstDay.getDate() + 6);
                    returnDateRange.start = this.formatDate(firstDay);
                    returnDateRange.end = this.formatDate(lastDay);
                    break;

                case "3" : // 이번달
                case "month" :
                    let thisMonthFirstDay = new Date(today.getFullYear(), today.getMonth(), 1);
                    let thisMonthLastDay = new Date(today.getFullYear(), today.getMonth() + 1, 0);
                    returnDateRange.start = this.formatDate(thisMonthFirstDay);
                    returnDateRange.end = this.formatDate(thisMonthLastDay);
                    break;

                case "4" : // 지난달
                    let lastMonthFirstDay = new Date(today.getFullYear(), today.getMonth() - 1, 1);
                    let lastMonthLastDay = new Date(today.getFullYear(), today.getMonth(), 0);
                    returnDateRange.start = this.formatDate(lastMonthFirstDay);
                    returnDateRange.end = this.formatDate(lastMonthLastDay);
                    break;
            }

            return returnDateRange;
        },

        // 팝업 생성
        createPopup(url, name, width = 700, height = 500) {
            const screenWidth = window.innerWidth || document.documentElement.clientWidth || document.body.clientWidth;
            const screenHeight = window.innerHeight || document.documentElement.clientHeight || document.body.clientHeight;
            const left = (screenWidth / 2) - (width / 2);
            const top = (screenHeight / 2) - (height / 2);
            const options = `width=${width},height=${height},left=${left},top=${top},scrollbars=yes,resizable=yes`;
            const popup = window.open(url, name, options);

            if (window.focus) {
                popup.focus();
            }
        },

        /**
         * 폼 초기화
         * @param name: form name
         * @param except: 초기화 제외 hidden name (array)
         */
        clearForm(name, except) {
            const form = document.querySelector(`form[name="${name}"]`);
            form.reset();

            const hiddenInputs = form.querySelectorAll('input[type="hidden"]');
            hiddenInputs.forEach(input => {
                if (except) {
                    if (!except.includes(input.name)) input.value = '';
                } else {
                    input.value = '';
                }
            });
        },

        /**
         * 엑셀다운로드
         * @param target: 엑셀구분(메뉴명..)
         */
        // async commonExcelDownload(target) {
        //     let parameter = (location.search ? location.search + '&' : '?') + `excel=${target}`;
        //
        //     if (target == 'pxOrderTracking' || target == 'pxOrderProductTracking') {
        //         // 주문배송관리 체크된 항목 추가
        //         const idxArr = Array.from(document.querySelectorAll('input[name="checkIdx"]:checked'))
        //             .map(checkbox => checkbox.value);
        //
        //         if (idxArr.length > 0) {
        //             parameter += `&idxArr=${encodeURIComponent(idxArr.join(','))}`;
        //         }
        //     }
        //
        //     location.href = `/excel/download${parameter}`;
        // },

        // 로딩 (show: true, false)
        showLoading(isShow) {
            const loading = document.getElementById("loading");
            if (loading) loading.classList.toggle('hide', !isShow);
        },

        // 쿠키 저장
        setCookie(name, value, hours) {
            const date = new Date();
            date.setTime(date.getTime() + (hours * 60 * 60 * 1000));
            //date.setTime(date.getTime() + (minutes*60*1000)); // 분
            document.cookie = `${name}=${value};expires=` + date.toUTCString() + ';path=/';
        },

        // 쿠키 조회
        getCookie(name) {
            const value = document.cookie.match('(^|;) ?' + name + '=([^;]*)(;|$)');
            return value ? value[2] : null;
        },

        // 이미지 유효성 검사
        validateImgFile(file, maxSizeMB = 5) {
            const maxByte = maxSizeMB * 1024 * 1024;

            if (file.size > maxByte) {
                this.showAlert(`${maxSizeMB}MB 이하 파일만 등록이 가능합니다.`);
                return false;
            }

            // 이미지 체크
            const regExt = /(.*?)\.(jpg|jpeg|png)$/;
            if (!regExt.test((file.name).toLowerCase())) {
                this.showAlert("올바른 형식의 이미지만 등록이 가능합니다.");
                return false;
            }

            return true;
        },

        // get 파라미터 가져오기
        getQueryParam(paramName) {
            const urlParams = new URLSearchParams(window.location.search);
            return urlParams.get(paramName);
        },

        // 사진 업로드 미리보기 이벤트 리스너
        bindImagePreview(uploadElementId, previewElementId) {
            const uploadElement = document.getElementById(uploadElementId);
            const previewElement = document.getElementById(previewElementId);
            if (!uploadElement) return;

            uploadElement.addEventListener('change', function () {
                const file = this.files[0];
                const reader = new FileReader();

                reader.onloadend = function () {
                    previewElement.style.backgroundImage = `url(${reader.result})`;
                };

                if (file) {
                    reader.readAsDataURL(file);
                    // } else {
                    //     const previewElement = document.getElementById(previewElementId);
                    //     previewElement.style.backgroundImage = '';
                }
            }, true);
        },

        // 미리보기 초기화
        initImagePreview(previewElementId, src) {
            const previewElement = document.getElementById(previewElementId);
            const noImg = (src) ? src : '/img/common/noimg.png';

            if (previewElement) previewElement.style.backgroundImage = `url(${noImg})`;
        },


        // 관심지역 `시` 변경시 `구` 호출
        async changeArea(siElement, gu) {
            const si = siElement.value;
            const guElement = document.querySelector(`[name="areaGu"]`);

            // if (si === '세종') { // 구 없음 -> `전체` 추가
            //     guElement.classList.add('hide');
            //     return;
            // }

            guElement.classList.remove('hide');

            const guList = await API.fetchData('/api/searchArea', {si});
            const creatOptions = ['<option value="전체">전체</option>'];

            if (guList && typeof guList == 'object') {
                // 구 목록을 배열로 변환하고 가나다 순으로 정렬
                const sortedGuList = Object.values(guList).sort((a, b) => a.localeCompare(b));

                sortedGuList.forEach(value => {
                    const selected = (gu && gu === value) ? 'selected' : '';
                    creatOptions.push(`<option value="${value}" ${selected}>${value}</option>`);
                });
            }

            guElement.innerHTML = creatOptions.join('');

            return {
                gu: guElement,
            };
        },

        // 지역(시, 구) 호출
        async getSiGuData() {
            try {
                const response = await fetch('/data/sigu.json');
                return await response.json();
            } catch (error) {
                console.error('Error loading sigu data:', error);
                return {};
            }
        },

        // 입력형식 확인 후 필터링
        filterInput(e) {
            const input = e.target;
            const format = input.dataset.format;
            if (!format) return;

            let value = input.value;

            switch (format) {
                case 'num':
                    if (value) input.value = utils.toNumber(value);
                    break;
                case 'decimal':
                    input.value = utils.toDecimal(value);
                    break;
                case 'tel':
                    input.value = utils.addHyphenTel(value);
                    break;
                case 'tel050':
                    input.value = utils.addHyphenTel050(value);
                    break;
                case 'trim': // 앞뒤 공백 제거
                    input.value = value.trim();
                    break;
                case 'ltrim' : // 앞의 공백만 제거
                    input.value = value.replace(/^\s+/, '');
                    break;
                case 'remove-space': // 모든 공백 제거
                    input.value = value.replace(/\s+/g, '');
                    break;
                case 'date' :
                    input.value = utils.addHyphenBirth(value);
                    break;
                case 'amount':
                    input.value = utils.addCommaNumber(value);
                    break;
                case 'max-num' : // 숫자 최대
                    value = utils.toNumber(value);
                    // const maxLength = input.maxLength > 0 ? input.maxLength : null;
                    // if (maxLength) input.value = String(value).slice(0, maxLength);
                    const maxCount = input.getAttribute('data-num') || 0;

                    if (maxCount > 0 && value > maxCount) input.value = maxCount;
                    else input.value = value;
                    break;
                case 'business' :
                    input.value = utils.businessNumber(value);
                    break;
            }
        },
        // 비동기 스크립트 로드
        loadScriptAsync(src, callback) {
            const script = document.createElement('script');
            script.src = src;
            script.async = true;  // 비동기 로드 설정
            script.onload = callback;  // 스크립트 로드 완료 후 콜백 함수 호출
            script.onerror = function () {
                console.error('Error loading script: ' + src);
            };
            document.head.appendChild(script);
        },
        // 공통 검색 스크립트 로드
        getSearchScript(formName) {
            const url = baseUrl + '/js/common/search.js';
            utils.loadScriptAsync(url, function () {
                if (typeof window.initSearch === 'function') {
                    window.initSearch(formName);
                }
            });
        },


        // 스와이퍼 생성
        createSwiper(className, addOptions = {}) {
            if (!document.querySelector(`${className}`)) return;

            const defaultOptions = {
                pagination: {
                    el: ".swiper-pagination",
                },
                autoplay: {
                    delay: 6000,
                    disableOnInteraction: false,
                },
            };

            const options = {...defaultOptions, ...addOptions};

            return new Swiper(className, options);
        },


        // 체크박스 선택
        checkboxes() {
            const checkAll = document.querySelector('#checkAll');
            const checkboxes = document.querySelectorAll('input[name="check"]');
            checkboxes.forEach(checkbox => {
                checkbox.checked = checkAll.checked;
            });
        },


    };
})();


document.addEventListener('DOMContentLoaded', () => {
    // 공통 input 입력 이벤트 리스너
    document.querySelectorAll('input[data-format]').forEach(input => {
        input.addEventListener('keyup', utils.filterInput);
    });

    // 동적 추가된 input 리스너
    const observer = new MutationObserver(mutations => {
        mutations.forEach(mutation => {
            if (mutation.type === 'childList') {
                mutation.addedNodes.forEach(node => {
                    if (node.tagName === 'INPUT' && node.dataset.format) {
                        node.addEventListener('keyup', utils.filterInput);
                    }
                });
            }
        });
    });
    observer.observe(document.body, {childList: true, subtree: true});
});

// 전역 오류 핸들러 설정
window.addEventListener('error', function (event) {
    const errorMessage = {
        eventName: 'error',
        url: location.href,
        message: event.message,
        source: event.filename,
        lineno: event.lineno,
        colno: event.colno,
        stack: event.error ? event.error.stack : null
    };
    sendErrorToServer(errorMessage);
});

window.addEventListener('unhandledrejection', function (event) {
    const errorMessage = {
        eventName: 'unhandledrejection',
        url: location.href,
        message: event.reason ? event.reason.message : 'Unhandled rejection',
        stack: event.reason ? event.reason.stack : null
    };
    sendErrorToServer(errorMessage);
});

function sendErrorToServer(errorMessage) {
    fetch(baseUrl + '/api/logError', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(errorMessage)
    }).then(response => {
        if (!response.ok) {
            console.error('Error logging failed:', response.statusText);
        }
    }).catch(error => {
        console.error('Error sending log to server:', error);
    });
}