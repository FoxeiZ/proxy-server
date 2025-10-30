/**
 * Proxy DOM Observer
 * This script is automatically injected by the base modifier for all proxied pages
 * It handles dynamic content modifications that bypass server-side processing
 */

(function () {
  "use strict";

  if (window.ProxyDOMObserver) {
    return;
  }

  const proxyConfig = {
    proxyBase: window.location.origin + "/",
    isProxyImages: (function () {
      const urlParams = new URLSearchParams(window.location.search);
      return urlParams.get("proxy_images") === "1";
    })(),
    baseHost: (function () {
      const path = window.location.pathname;
      const match = path.match(/^\/p\/([^\/]+)/);
      return match ? match[1] : window.location.host;
    })(),
    debug: false,
  };

  function log(...args) {
    if (proxyConfig.debug) {
      console.log("[ProxyDOMObserver]", ...args);
    }
  }

  /**
   * Check if URL should be processed
   */
  function shouldProcessURL(url) {
    if (!url || typeof url !== "string") {
      return false;
    }

    // Skip these URL types
    const skipPrefixes = [
      "javascript:",
      "data:",
      "mailto:",
      "tel:",
      "..",
      "/p/",
      proxyConfig.proxyBase,
    ];

    return (
      !skipPrefixes.some((prefix) => url.startsWith(prefix)) &&
      !url.includes(window.location.host)
    );
  }

  /**
   * from base.py
   */
  function transformURL(url, tagName) {
    if (!shouldProcessURL(url)) {
      return url;
    }

    let newURL = url;
    try {
      if (url.startsWith("http://") || url.startsWith("https://")) {
        if (tagName === "img" && !proxyConfig.isProxyImages) {
          return url;
        }
        const urlObj = new URL(url);
        // f"{proxy_base}p/{url_parts.netloc}/{url_parts.path.lstrip('/')}"
        newURL = `${proxyConfig.proxyBase}p/${urlObj.host}${
          urlObj.pathname.substring(1) || ""
        }${urlObj.search}${urlObj.hash}`;
      }
      // relative URLs "//""
      else if (url.startsWith("//")) {
        if (tagName === "img" && !proxyConfig.isProxyImages) {
          // f"{page_url_parts.scheme}:{url}"
          newURL = `${window.location.protocol}${url}`;
        } else {
          // f"/p/{url.lstrip('/')}"
          newURL = `/p/${url.substring(2)}`;
        }
      }
      // relative URLs (not starting with /)
      else if (!url.startsWith("/")) {
        if (tagName === "a") {
          const pathSegments = window.location.pathname
            .replace(/^\/p\/[^\/]+/, "")
            .split("/")
            .filter(Boolean);
          if (pathSegments.length > 0) {
            pathSegments.pop();
          }
          newURL = `/p/${proxyConfig.baseHost}/${pathSegments.join(
            "/"
          )}/${url}`.replace(/\/+/g, "/");
        } else if (tagName === "img" && !proxyConfig.isProxyImages) {
          // f"{page_url_parts.scheme}://{page_url_parts.netloc}{url}"
          newURL = `${window.location.protocol}//${proxyConfig.baseHost}/${url}`;
        } else {
          // f"{request_url.rstrip('/')}/{url.lstrip('/')}"
          const currentPath = window.location.pathname.replace(/\/$/, "");
          newURL = `${currentPath}/${url}`;
        }
      }
      // root-relative URLs
      else {
        if (tagName === "img" && !proxyConfig.isProxyImages) {
          // f"{page_url_parts.scheme}://{page_url_parts.netloc}{url}"
          newURL = `${window.location.protocol}//${proxyConfig.baseHost}${url}`;
        } else {
          // f"/p/{base_url}/{url.lstrip('/')}"
          newURL = `/p/${proxyConfig.baseHost}${url}`;
        }
      }
    } catch (e) {
      log("Failed to transform URL:", url, e);
      return url;
    }

    console.log("Transformed URL:", url, "->", newURL);
    return newURL;
  }

  /**
   * Modify a single element's attribute
   */
  function modifyElement(element, attribute) {
    const processedAttr = `data-proxy-${attribute}-processed`;

    // Skip if already processed
    if (element.hasAttribute(processedAttr)) {
      return;
    }

    const url = element.getAttribute(attribute);
    if (!url) {
      return;
    }

    const tagName = element.tagName.toLowerCase();
    const newURL = transformURL(url, tagName);

    if (newURL !== url) {
      element.setAttribute(attribute, newURL);
      element.setAttribute(processedAttr, "true");
      log(`Modified ${tagName}[${attribute}]:`, url, "->", newURL);
    }
  }

  /**
   * Process all relevant elements in a node
   */
  function processNode(node) {
    if (!node || node.nodeType !== Node.ELEMENT_NODE) {
      return;
    }

    // NOTE: We DON'T process data-src/data-original here - we let the website's lazy loading
    // script set the src, then we catch that change and transform it
    const processTypes = [
      ["a", "href"],
      ["img", "src"],
      ["link", "href"],
      ["script", "src"],
      ["form", "action"],
    ];

    // Process the node itself
    processTypes.forEach(([tag, attr]) => {
      if (
        node.tagName &&
        node.tagName.toLowerCase() === tag &&
        node.hasAttribute(attr)
      ) {
        modifyElement(node, attr);
      }
    });

    // Process all descendants
    processTypes.forEach(([tag, attr]) => {
      const elements = node.querySelectorAll(`${tag}[${attr}]`);
      elements.forEach((element) => modifyElement(element, attr));
    });

    // Special handling for lazy-loaded images - watch for src changes
    handleLazyImages(node);
  }

  /**
   * Handle lazy-loaded images specifically
   * We DON'T modify data-src/data-original - we let the website's lazy loading script
   * copy data-src to src, then we catch that src change and transform it
   */
  function handleLazyImages(node) {
    const lazySelectors = [
      "img[data-src]",
      "img[data-original]",
      'img[loading="lazy"]',
      "img.lazy",
      "img.lazyload",
    ];

    const lazyImages = node.querySelectorAll(lazySelectors.join(", "));

    lazyImages.forEach((img) => {
      // DON'T process data attributes directly - let the website handle that
      // Instead, watch for when the lazy loading script sets the src attribute

      if (!img.hasAttribute("data-proxy-lazy-observer")) {
        img.setAttribute("data-proxy-lazy-observer", "true");

        const srcObserver = new MutationObserver((mutations) => {
          mutations.forEach((mutation) => {
            if (
              mutation.type === "attributes" &&
              mutation.attributeName === "src"
            ) {
              // When src gets set by lazy loading, transform it
              log("Lazy loading set src attribute, transforming...");
              modifyElement(mutation.target, "src");
            }
          });
        });

        srcObserver.observe(img, {
          attributes: true,
          attributeFilter: ["src"],
        });
      }
    });

    // Handle the node itself if it's a lazy image
    if (node.tagName && node.tagName.toLowerCase() === "img") {
      const hasLazyAttr = ["data-src", "data-original", "loading"].some(
        (attr) => node.hasAttribute(attr)
      );

      if (hasLazyAttr && !node.hasAttribute("data-proxy-lazy-observer")) {
        node.setAttribute("data-proxy-lazy-observer", "true");

        // Watch for src attribute changes only
        const srcObserver = new MutationObserver((mutations) => {
          mutations.forEach((mutation) => {
            if (
              mutation.type === "attributes" &&
              mutation.attributeName === "src"
            ) {
              log("Lazy loading set src on node, transforming...");
              modifyElement(mutation.target, "src");
            }
          });
        });

        srcObserver.observe(node, {
          attributes: true,
          attributeFilter: ["src"],
        });
      }
    }
  }

  /**
   * Initialize the DOM observer
   */
  function init() {
    log("Initializing Proxy DOM Observer", proxyConfig);

    // Process existing content first
    if (document.body) {
      processNode(document.body);
    }

    // Set up mutation observer for new content
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.type === "childList") {
          mutation.addedNodes.forEach((node) => {
            if (node.nodeType === Node.ELEMENT_NODE) {
              processNode(node);
            }
          });
        } else if (mutation.type === "attributes") {
          const target = mutation.target;
          if (target.nodeType === Node.ELEMENT_NODE) {
            modifyElement(target, mutation.attributeName);
          }
        }
      });
    });

    observer.observe(document.body || document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["src", "data-src", "data-original", "href", "action"],
    });

    log("Proxy DOM Observer initialized successfully");

    return observer;
  }

  // Wait for DOM to be ready
  function initWhenReady() {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", init);
    } else {
      init();
    }
  }

  // Public API
  window.ProxyDOMObserver = {
    observer: null,
    processNode: processNode,
    modifyElement: modifyElement,
    transformURL: transformURL,
    config: proxyConfig,
    init: init,
    enable: () => {
      proxyConfig.debug = true;
    },
    disable: () => {
      proxyConfig.debug = false;
    },
  };

  // Auto-initialize
  initWhenReady();
})();
