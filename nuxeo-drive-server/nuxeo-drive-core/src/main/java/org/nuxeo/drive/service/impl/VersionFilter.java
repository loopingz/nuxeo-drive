/*
 * (C) Copyright 2014 Nuxeo SA (http://nuxeo.com/) and contributors.
 *
 * All rights reserved. This program and the accompanying materials
 * are made available under the terms of the GNU Lesser General Public License
 * (LGPL) version 2.1 which accompanies this distribution, and is available at
 * http://www.gnu.org/licenses/lgpl-2.1.html
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
 * Lesser General Public License for more details.
 *
 * Contributors:
 *     looping
 */
package org.nuxeo.drive.service.impl;

import java.io.IOException;

import javax.servlet.Filter;
import javax.servlet.FilterChain;
import javax.servlet.FilterConfig;
import javax.servlet.ServletException;
import javax.servlet.ServletRequest;
import javax.servlet.ServletResponse;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

/**
 * 
 * 
 * @since TODO
 */
public class VersionFilter implements Filter {

    /**
     * Minimal version
     */
    protected Integer[] minimalVersion = { 0, 0, 0 };

    protected boolean checkVersion(String userAgent) {
        // If the request came from a valid drive agent (
        // NuxeoDrive/versionNumber )
        if (userAgent != null
                && userAgent.matches("Nuxeo Drive/[0-9]+\\.[0-9]+\\.[0-9]")) {
            // Get the version
            userAgent = userAgent.substring(userAgent.indexOf("/") + 1);
            String[] versions = userAgent.split("\\.");
            for (int i = 0; i < minimalVersion.length; i++) {
                Integer current = Integer.valueOf(versions[i]);
                // Ahead on version don't check the next one
                if (current > minimalVersion[i]) {
                    break;
                } else if (current < minimalVersion[i]) { // Below wanted
                                                          // version
                    return false;
                }
            }
        }
        return true;
    }

    @Override
    public void doFilter(ServletRequest request, ServletResponse response,
            FilterChain chain) throws IOException, ServletException {
        if (request instanceof HttpServletRequest) {
            HttpServletRequest httpRequest = (HttpServletRequest) request;
            if (checkVersion(httpRequest.getHeader("User-Agent"))) {
                HttpServletResponse httpResponse = (HttpServletResponse) response;
                httpResponse.sendError(426); // Need update from client
            }
        }
        chain.doFilter(request, response);
    }

    @Override
    public void init(FilterConfig filterConfig) throws ServletException {
        // Do nothing
    }

    @Override
    public void destroy() {
        // Do nothing
    }
}
