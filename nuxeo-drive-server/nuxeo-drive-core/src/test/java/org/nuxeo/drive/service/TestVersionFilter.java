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
package org.nuxeo.drive.service;

import junit.framework.Assert;

import org.junit.Test;
import org.nuxeo.drive.service.impl.VersionFilter;

/**
 * 
 * 
 * @since TODO
 */
public class TestVersionFilter extends VersionFilter {

    private final Integer[] testVersion = { 1, 3, 4 };

    @Test
    public void testMinimalVersion() {
        this.minimalVersion = testVersion;
        // Same version as minimal
        Assert.assertTrue(checkVersion("Nuxeo Drive/1.3.4"));
        // Upper version
        Assert.assertTrue(checkVersion("Nuxeo Drive/1.3.5"));
        Assert.assertTrue(checkVersion("Nuxeo Drive/1.4.1"));
        Assert.assertTrue(checkVersion("Nuxeo Drive/2.1.1"));
        // Lower version
        Assert.assertFalse(checkVersion("Nuxeo Drive/1.2.4"));
        Assert.assertFalse(checkVersion("Nuxeo Drive/1.3.3"));
        Assert.assertFalse(checkVersion("Nuxeo Drive/0.2.4"));
        // Not a NuxeoDrive
        Assert.assertTrue(checkVersion("Loopz Drive/1.3.4"));
        // Not a valid nuxeo drive version
        Assert.assertTrue(checkVersion("Nuxeo Drive/1.3.4.092"));
        // Development version is not blocked yet
        Assert.assertTrue(checkVersion("Nuxeo Drive/1.3-dev"));
    }
}
