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
 *     Antoine Taillefer <ataillefer@nuxeo.com>
 */
package org.nuxeo.drive.operations;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;

import java.util.Arrays;
import java.util.HashMap;
import java.util.Map;

import org.apache.commons.io.IOUtils;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.nuxeo.ecm.automation.client.Session;
import org.nuxeo.ecm.automation.client.jaxrs.impl.HttpAutomationClient;
import org.nuxeo.ecm.automation.client.model.Blob;
import org.nuxeo.ecm.automation.client.model.Document;
import org.nuxeo.ecm.automation.client.model.StringBlob;
import org.nuxeo.ecm.automation.core.operations.blob.AttachBlob;
import org.nuxeo.ecm.automation.core.operations.blob.GetDocumentBlob;
import org.nuxeo.ecm.automation.core.operations.document.CreateDocument;
import org.nuxeo.ecm.automation.core.operations.document.SetDocumentACE;
import org.nuxeo.ecm.automation.test.EmbeddedAutomationServerFeature;
import org.nuxeo.ecm.core.api.ClientException;
import org.nuxeo.ecm.core.api.CoreSession;
import org.nuxeo.ecm.core.api.DocumentModel;
import org.nuxeo.ecm.core.api.security.SecurityConstants;
import org.nuxeo.ecm.directory.api.DirectoryService;
import org.nuxeo.runtime.test.runner.Features;
import org.nuxeo.runtime.test.runner.FeaturesRunner;
import org.nuxeo.runtime.test.runner.Jetty;

import com.google.inject.Inject;

/**
 * Tests is the server-side transaction is commited when the HTTP response is
 * read by the {@link HttpAutomationClient}.
 *
 * @author Antoine Taillefer
 */
@RunWith(FeaturesRunner.class)
@Features(EmbeddedAutomationServerFeature.class)
@Jetty(port = 18080)
public class TestTxCommitedWhenResponseRead {

    private static final int LOOP_COUNT = 1000;

    @Inject
    protected CoreSession session;

    @Inject
    protected DirectoryService directoryService;

    @Inject
    protected HttpAutomationClient automationClient;

    @Inject
    protected Session adminSession;

    protected Session user1Session;

    @Before
    public void init() throws Exception {

        // Create test user: user1
        org.nuxeo.ecm.directory.Session userDir = directoryService.getDirectory(
                "userDirectory").getSession();
        try {
            Map<String, Object> user1 = new HashMap<String, Object>();
            user1.put("username", "user1");
            user1.put("password", "user1");
            user1.put("groups", Arrays.asList(new String[] { "members" }));
            userDir.createEntry(user1);
        } finally {
            userDir.close();
        }

        // Get an Automation client session as user1
        user1Session = automationClient.getSession("user1", "user1");
    }

    @After
    public void tearDown() throws ClientException {
        org.nuxeo.ecm.directory.Session userDir = directoryService.getDirectory(
                "userDirectory").getSession();
        try {
            userDir.deleteEntry("user1");
        } finally {
            userDir.close();
        }
    }

    @Test
    public void testAttachBlob() throws Exception {

        for (int i = 0; i < LOOP_COUNT; i++) {
            // Create a File document
            DocumentModel file = session.createDocumentModel("/", "testFile",
                    "File");
            file = session.createDocument(file);
            session.save();

            // Attach a blob to the document
            StringBlob blob = new StringBlob(
                    "This is the content of a new file.");
            blob.setFileName("New file.txt");
            blob.setMimeType("text/plain");
            adminSession.newRequest(AttachBlob.ID).set("document",
                    file.getPathAsString()).setInput(blob).execute();

            // Get blob from document
            Blob clientBlob = (Blob) adminSession.newRequest(GetDocumentBlob.ID).setInput(
                    file.getPathAsString()).execute();
            assertNotNull(clientBlob);
            String blobString = new String(
                    IOUtils.toByteArray(clientBlob.getStream()));
            assertEquals("This is the content of a new file.", blobString);
        }
    }

    @Test
    public void testSetDocumentACE() throws Exception {

        for (int i = 0; i < LOOP_COUNT; i++) {
            // Create a Folder document as Administrator
            DocumentModel folder = session.createDocumentModel("/",
                    "testFolder", "Folder");
            folder = session.createDocument(folder);
            session.save();

            // Grant ReadWrite permission to user1 on folder
            adminSession.newRequest(SetDocumentACE.ID).setInput(
                    folder.getPathAsString()).set("user", "user1").set(
                    "permission", SecurityConstants.READ_WRITE).set("grant",
                    true).execute();

            // Create a Folder document inside folder as user1
            Document subFolder = (Document) user1Session.newRequest(
                    CreateDocument.ID).setInput(folder.getPathAsString()).set(
                    "type", "Folder").set("name", "subFolder").execute();
            assertNotNull(subFolder);
        }
    }

}
