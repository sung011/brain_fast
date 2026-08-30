/**
 * 비동기 통신 모듈
 */
const API = (function () {
    return {
        // AJAX POST, GET
        async fetchData(url, bodyData, method = "POST") {
            let requestOptions = {
                method: method, headers: {}, body: bodyData,
            };

            if (typeof bodyData === "object" && !(bodyData instanceof FormData)) {
                requestOptions.body = JSON.stringify(bodyData);
                requestOptions.headers["Content-Type"] = "application/json";
                requestOptions.headers["X-Requested-With"] = "XMLHttpRequest";
            }

            // GET 요청시 파라미터는 queryString으로만
            if (method == "GET") requestOptions = null;

            try {
                const domain = baseUrl //(baseUrl.endsWith('/')) ? baseUrl.slice(0, -1) : baseUrl;
                const response = await fetch(domain + url, requestOptions);
                const data = await response.json();

                if (!response.ok) {
                    // response 에러 -> catch
                    throw new Error('Network response was not ok');
                }

                return data;

            } catch (error) {
                // console.log('fetchJSON() error:\n', error);
                // throw error;
                return {result: false, message: '서버와의 통신에 실패했습니다.'};

            } finally {
                utils.showLoading(0);
            }
        },

        // AJAX HTML page load
        async fetchHtml(url, element, method) {
            const fetchResult = {result: false, message: '서버와의 통신에 실패했습니다.'};
            try {
                const domain = '';//(baseUrl.endsWith('/')) ? baseUrl.slice(0, -1) : baseUrl;
                const httpResponse = await fetch(domain + url);
                if (!httpResponse.ok) {
                    throw new Error(`HTTP error! status: ${httpResponse.status}`);
                }
                const content = await httpResponse.text();

                if (element && content) {
                    if (method === "append") {
                        element.insertAdjacentHTML('beforeend', content);
                    } else {
                        element.innerHTML = content;
                    }

                    fetchResult.result = true;
                    fetchResult.message = '성공적으로 처리되었습니다.';
                    fetchResult.content = content;
                }

            } catch (error) {
                console.log('fetchJSON() error:\n', error);
                // throw error;
            }
            return fetchResult;
        },

        // AJAX Excel
        async fetchExcel(url, fileName) {
            try {
                utils.showLoading(true);

                if (!fileName) fileName = '엑셀다운';

                // 파일 다운로드 요청
                const response = await fetch(url);
                if (!response.ok) throw new Error('서버와의 통신에 실패했습니다.');

                const blob = await response.blob();
                const downloadUrl = URL.createObjectURL(blob);

                const a = document.createElement("a");
                a.href = downloadUrl;
                a.download = `${fileName}.xlsx`;
                document.body.appendChild(a);
                a.click();

                URL.revokeObjectURL(downloadUrl);
                document.body.removeChild(a);

                setTimeout(() => {
                    utils.showLoading(0);
                }, 500);

            } catch (error) {
                utils.showLoading(0);
                utils.showError(error);

            } finally {
                //
            }
        },

        /**
         * fetchHtml 페이징
         * @param page: 현재페이지
         * @param formName: 검색 form
         * @param modalFunc: 실행 function
         */
        fetchHtmlPaging(page, formName, modalFunc) {
            if (!page || !modalFunc) return;
            if (formName && document[formName]) document[formName].page.value = page;

            modalFunc();
        },

    };
})();
